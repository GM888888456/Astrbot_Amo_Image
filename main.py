# -*- coding: utf-8 -*-
"""我想和你度日常（Astrbot_Amo_Image）：生图联动。

给「我想永远陪伴你」（Astrbot_Amo_forever）和「我想永远记住你」（Astr_Amo_data）
配套的第三块：**只负责画图**。

- 人设外观从 `character_appearance` 读，拼在提示词最前面，保证每次是同一个人；
- 上下文从数据插件的记忆接口读（`recall_messages` / `messages_since`），让画面对得上剧情；
- 后端走 OpenAI 兼容的 `POST {api_base}/images/generations`，同时支持 `url` 与 `b64_json`；
- 找不到数据插件、后端报错、超时、返回空——一律降级成人设化的一句话，不抛异常。

它不参与聊天判定、不改 AstrBot 核心链路，也不依赖另外两个插件必须在场。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import tempfile
import time
import unicodedata
import zlib
from collections import deque

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star

try:  # AstrBot 4.x
    from astrbot.api.message_components import Image, Plain, Reply
except Exception:  # pragma: no cover - 老版本路径
    from astrbot.core.message.components import Image, Plain, Reply  # type: ignore

try:  # 面板后端：AstrBot 4.x 的插件 Web API
    from astrbot.api.web import error_response, json_response, request as web_request
except Exception:  # pragma: no cover - 老版本没有该模块
    error_response = json_response = web_request = None

try:
    import aiohttp
except Exception:  # pragma: no cover - 没装 aiohttp 时退回线程 + urllib
    aiohttp = None  # type: ignore

class APIError(RuntimeError):
    """后端返回 >=400 时抛这个，带上状态码方便决定要不要换一套参数重试。"""

    def __init__(self, status, message):
        super().__init__("生图接口 %s: %s" % (status, message))
        self.status = int(status or 0)


PLUGIN_NAME = "Astrbot_Amo_Image"
PLUGIN_VERSION = "0.11.0"

# 服装池留空时的保底：宁可穿得普通，也不能每次随机换一身
DEFAULT_OUTFITS = [
    "米色针织开衫配浅蓝牛仔裤",
    "白色棉质衬衫配深色半身裙",
    "浅灰连帽卫衣配宽松长裤",
    "碎花连衣裙外搭薄开衫",
    "藏青色针织衫配卡其长裤",
    "奶油色毛衣配直筒长裤",
]

# 负面提示词：支持 negative_prompt 的后端（Kolors / SiliconFlow 等）会用它，
# 不支持的（OpenAI images）会自动去掉这个字段重试，不会因此报错。
DEFAULT_NEGATIVE_PROMPT = (
    "different face, different hairstyle, different clothes, different person, "
    "change location, other scenic spot, blurry, low quality, extra fingers"
)

# 「换一套衣服」这类手动换装：整句就是指令，不能当成生图提示词
OUTFIT_COMMANDS = ("换一套衣服", "换套衣服", "换身衣服", "换衣服", "换一件衣服")

# 内容边界：私聊只拦露骨裸体 / 性器官 / 性行为 / 未成年相关；
# 群聊额外拦「只穿内衣 / 比基尼 / 情趣内衣」这类。这是一层关键词防线，
# 挡不住谐音、外语、拆字和极端变体，别当唯一保险。
EXPLICIT_TERMS = (
    "裸体", "裸體", "全裸", "赤裸", "一丝不挂", "未着寸缕",
    "露阴", "阴部", "私处", "生殖器", "下体裸露",
    "性交", "做爱", "口交", "肛交", "性行为", "自慰", "射精",
    "porn", "nude", "naked", "genital", "explicitsex",
)
MINOR_TERMS = (
    "幼女", "未成年", "儿童色情", "未成年性", "childporn", "loli", "萝莉",
)
INJECTION_TERMS = (
    "忽略之前", "忽略以上", "ignoreprevious", "ignoreabove",
    "systemprompt", "系统提示", "越狱", "jailbreak", "不要遵守",
)

# 群聊额外档：内衣 / 比基尼 / 走光。文本已归一化（小写、去空白），
# 所以英文词也要写成连写形式。
_GROUP_BLOCK_PATTERNS = (
    re.compile(r"(只|仅|单)穿.{0,6}(内衣|泳衣|泳装|比基尼)"),
    re.compile(r"(穿|着).{0,4}内衣"),
    re.compile(r"内衣.{0,4}(照|写真|自拍)"),
    re.compile(r"情趣内衣|蕾丝内衣|比基尼|bikini|lingerie"),
    re.compile(r"露乳|露臀|走光|underwearonly|swimsuitonly"),
)

DATA_PLUGIN_NAMES = (
    "Astr_Amo_data",
    "astrbot_plugin_amo_data",
    "amo_data",
)

DEFAULT_KEYWORDS = ["画一张", "画个", "帮我画", "生成图片", "来张图", "画张"]
DEFAULT_PHOTO_KEYWORDS = ["拍张照", "拍一张", "拍个照", "拍张照片", "拍一张照片",
                          "自拍", "来张自拍", "发张照片"]
# 「给我拍张照」去掉触发词后只剩「给我」，这些词不该当提示词
_FILLER_WORDS = ("给我", "帮我", "帮忙", "请你", "请", "你", "我", "一张", "张",
                 "照片", "图片", "一下", "吧", "呗", "看看")

# 触发词后面常跟的标点/空白，去掉再当提示词
_TRIM_CHARS = " \t\r\n，,。.：:；;、!！?？\"'“”‘’"

_UNSAFE_PROMPT = re.compile(r"[\x00-\x1f]")


def clean_prompt(text: str, keyword: str) -> str:
    """把「画一张 一只在雨里的猫」里的触发词和标点去掉，只留提示词。"""
    body = str(text or "")
    if keyword:
        idx = body.find(keyword)
        if idx >= 0:
            body = body[idx + len(keyword):]
    body = _UNSAFE_PROMPT.sub(" ", body)
    return body.strip(_TRIM_CHARS)


def strip_filler(text: str) -> str:
    """把「给我 / 帮我 / 一下」这类没信息量的词去掉，剩下的才当提示词。"""
    body = str(text or "").strip()
    changed = True
    while changed and len(body) <= 6:
        changed = False
        for word in _FILLER_WORDS:
            if body.startswith(word) and len(body) > len(word):
                body = body[len(word):].strip(_TRIM_CHARS)
                changed = True
            elif body.endswith(word) and len(body) > len(word):
                body = body[: -len(word)].strip(_TRIM_CHARS)
                changed = True
    return body


def match_keyword(text: str, keywords) -> str:
    """返回命中的触发词（没命中返回空串）。长的优先，避免「画个」抢了「画个图」。"""
    body = str(text or "")
    for word in sorted([str(k) for k in (keywords or []) if str(k)], key=len, reverse=True):
        if word and word in body:
            return word
    return ""


def _as_url(value):
    text = str(value or "").strip()
    return text if text.startswith("http://") or text.startswith("https://") else ""


def _as_b64(value):
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("data:"):
        text = text.split(",", 1)[-1]
    text = text.strip()
    # 真图片的 base64 不会短；顺便 validate，避免把普通单词当图
    if len(text) < 40:
        return None
    try:
        return base64.b64decode(text, validate=True)
    except Exception:
        return None


def _walk_candidates(node, depth=0):
    """把各家返回结构摊平成「可能是 url / base64 的叶子」。

    见过放图的地方不止一种：data[]、images[]、artifacts[]、results[]、
    output.urls[]、result.images[]……这里不认厂商，只认形状。
    """
    if depth > 4:
        return
    if isinstance(node, str):
        yield node
        return
    if isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk_candidates(item, depth + 1)
        return
    if not isinstance(node, dict):
        return
    if any(key in node for key in ("url", "image_url", "imageUrl", "b64_json", "b64", "base64")):
        yield node
    for key in ("data", "images", "artifacts", "results", "output", "outputs",
                "result", "image", "urls", "items"):
        if key in node:
            yield from _walk_candidates(node[key], depth + 1)


def parse_image_response(payload) -> tuple:
    """解析生图响应，返回 (kind, value)：kind 是 url 或 b64。

    不绑定厂商：OpenAI 的 data[0].url / data[0].b64_json、SiliconFlow 的
    images[0].url、以及 output.urls[] / artifacts[].base64 之类都能认。
    """
    if isinstance(payload, (str, list)):
        candidates = _walk_candidates(payload)
    elif isinstance(payload, dict):
        candidates = _walk_candidates(payload)
    else:
        raise ValueError("响应不是 JSON")

    for item in candidates:
        if isinstance(item, str):
            url = _as_url(item)
            if url:
                return ("url", url)
            data = _as_b64(item)
            if data:
                return ("b64", data)
            continue
        if not isinstance(item, dict):
            continue
        for key in ("url", "image_url", "imageUrl"):
            url = _as_url(item.get(key))
            if url:
                return ("url", url)
        for key in ("b64_json", "b64", "base64", "image_base64"):
            data = _as_b64(item.get(key))
            if data:
                return ("b64", data)
    raise ValueError("响应里找不到图片（data / images / artifacts 均无 url 或 base64）")


def build_prompt(appearance: str, prompt: str, context_lines=None, look: str = "",
                 place: str = "") -> str:
    """拼最终提示词：外观 → 今日固定造型 → 此刻地点 → 用户要求 → 剧情上下文。

    look / place 是为了「同一个人、同一天不换衣服、不瞬移」：造型按天固定，
    地点来自日程里此刻那一格，不是随机换。
    """
    parts = []
    if str(appearance or "").strip():
        parts.append(str(appearance).strip())
    body = str(prompt or "")
    # 陪伴那边的分享模板本身就带了 {place}，这里别再叠一遍「此刻在××」
    if str(look or "").strip() and str(look).strip() not in body:
        parts.append("今天的样子：" + str(look).strip())
    if str(place or "").strip() and str(place).strip() not in body:
        parts.append("此刻在" + str(place).strip())
    if body.strip():
        parts.append(body.strip())
    if context_lines:
        tail = "；".join([str(x).strip() for x in context_lines if str(x).strip()][-6:])
        if tail:
            parts.append("画面要接得上这段对话：%s" % tail)
    if str(appearance or "").strip() or str(look or "").strip():
        parts.append("同一个人，脸、发型、服装保持一致，不要换成别的装扮")
    return "，".join(parts)


class AmoImagePlugin(Star):
    """生图插件：认人设、接上下文、只画图。"""

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self._last_call = {}   # uid -> 上次生图时间
        self._daily = {}       # uid -> [日期字符串, 次数]
        self._daily_group = {} # umo -> [日期字符串, 次数]
        self._history = deque(maxlen=40)   # 最近几次生图（面板用）
        self._style_cache = {}             # endpoint -> 试通过的参数风格
        self._negative_ok = {}             # endpoint -> 后端认不认 negative_prompt
        self._look_state = None            # 手动换装记录（按天，落盘）
        self._calls_total = 0
        self._started_at = time.time()
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self._register_web_apis()

    # ---------- 配置 ----------

    def _cfg(self, name, default=None):
        value = self.config.get(name, default)
        return default if value is None else value

    def _bool(self, name, default=False):
        value = self._cfg(name, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on", "是")

    def _int(self, name, default=0):
        try:
            return int(float(self._cfg(name, default)))
        except Exception:
            return default

    def _float(self, name, default=0.0):
        try:
            return float(self._cfg(name, default))
        except Exception:
            return default

    def _keywords(self):
        value = self._cfg("keywords", DEFAULT_KEYWORDS)
        if isinstance(value, str):
            value = [x.strip() for x in value.split(",")]
        return [str(x) for x in (value or DEFAULT_KEYWORDS) if str(x).strip()]

    def _photo_keywords(self):
        value = self._cfg("photo_keywords", DEFAULT_PHOTO_KEYWORDS)
        if isinstance(value, str):
            value = [x.strip() for x in value.split(",")]
        return [str(x) for x in (value or []) if str(x).strip()]

    # ---------- 数据插件联动 ----------

    @staticmethod
    def _star_instance(meta, require_mem=True):
        """AstrBot 各版本把实例放在不同属性上，挨个试。

        require_mem=True 用于找数据插件（必须带 mem）；False 用于找陪伴主插件。
        """
        if meta is None:
            return None
        for attr in ("star_cls", "instance", "star", "plugin"):
            value = getattr(meta, attr, None)
            if value is None:
                continue
            if not require_mem or hasattr(value, "mem"):
                return value
        return None

    def _data_plugin(self):
        """找「我想永远记住你」的实例；没装就 None。"""
        try:
            for name in DATA_PLUGIN_NAMES:
                star = self._star_instance(self.context.get_registered_star(name))
                if star is not None:
                    return star
        except Exception:
            pass
        try:
            for meta in self.context.get_all_stars():
                marker = (getattr(meta, "name", "") or "") + " " + (getattr(meta, "module_path", "") or "")
                star = self._star_instance(meta)
                if star is not None and "amo_data" in marker.lower():
                    return star
        except Exception:
            pass
        return None

    COMPANION_NAMES = ("Astrbot_Amo_forever", "astrbot_plugin_amo_forever", "amo_forever")

    def _companion(self):
        """找陪伴主插件实例；找不到返回 None。"""
        try:
            for name in self.COMPANION_NAMES:
                star = self._star_instance(self.context.get_registered_star(name), require_mem=False)
                if star is not None:
                    return star
        except Exception:
            pass
        try:
            for meta in self.context.get_all_stars():
                marker = (getattr(meta, "name", "") or "") + " " + (getattr(meta, "module_path", "") or "")
                if "amo_forever" not in marker.lower():
                    continue
                star = self._star_instance(meta, require_mem=False)
                if star is not None:
                    return star
        except Exception:
            pass
        return None

    def _bot_id(self) -> str:
        """和陪伴插件用同一个 bot_id，否则数据插件里的聊天记录读不到。"""
        companion = self._companion()
        if companion is not None:
            getter = getattr(companion, "_data_bot_id", None)
            if callable(getter):
                try:
                    value = str(getter() or "").strip()
                    if value:
                        return value
                except Exception:
                    pass
            conf = getattr(companion, "config", None) or {}
            value = str(conf.get("data_bot_id") or conf.get("bot_name") or "").strip()
            if value:
                return value
        plugin = self._data_plugin()
        conf = getattr(plugin, "config", None) or {}
        return str(conf.get("bot_id") or "").strip()

    def _mem(self):
        plugin = self._data_plugin()
        return getattr(plugin, "mem", None) if plugin is not None else None

    async def _context_lines(self, umo: str, uid: str):
        """取最近几条对话当画面参考；任何异常都静默返回空列表。"""
        if not self._bool("use_memory_context", True):
            return []
        mem = self._mem()
        if mem is None:
            return []
        limit = max(1, self._int("memory_context_limit", 6))
        lines = []
        try:
            bot_id = self._bot_id()
            if callable(getattr(mem, "messages_since", None)):
                rows = await mem.messages_since(bot_id, uid=uid, since_ts=0, limit=limit)
            elif callable(getattr(mem, "recall_messages", None)):
                got = await mem.recall_messages(bot_id, query="", umo=umo, uid=uid, limit=limit)
                rows = got.get("messages") if isinstance(got, dict) else got
            else:
                rows = []
            for row in rows or []:
                if isinstance(row, dict):
                    text = row.get("content") or row.get("text") or ""
                else:
                    text = str(row)
                text = str(text).strip()
                if text:
                    lines.append(text[:120])
        except Exception as exc:
            logger.debug("[amo_image] 取上下文失败，跳过: %s" % exc)
            return []
        return lines[-limit:]

    # ---------- 闸门 ----------

    def _is_group(self, event) -> bool:
        try:
            return not bool(event.is_private_chat())
        except Exception:
            return False

    @staticmethod
    def _group_from_umo(umo) -> bool:
        """分享入口没有 event，只能从 UMO 判断是不是群聊。"""
        return "groupmessage" in str(umo or "").lower()

    @staticmethod
    def _normalize_text(text: str) -> str:
        """归一化：全角转半角、转小写、去掉空白和零宽字符，防「裸 体」这类拆字。"""
        value = unicodedata.normalize("NFKC", str(text or "")).lower()
        return re.sub(r"[\s\u200b\u200c\u200d\ufeff]+", "", value)

    def _safe_text(self, text: str, group: bool = False) -> bool:
        """输入边界：返回 True 表示可以画。

        私聊只拦露骨裸体 / 性器官 / 性行为 / 未成年相关；
        群聊额外拦「只穿内衣 / 比基尼 / 情趣内衣」这类。
        """
        if not self._bool("safe_mode", True):
            return True
        raw = self._normalize_text(text)
        if not raw:
            return True
        if len(raw) > 600:
            return False
        for term in EXPLICIT_TERMS + MINOR_TERMS + INJECTION_TERMS:
            if self._normalize_text(term) in raw:
                return False
        if group and any(pattern.search(raw) for pattern in _GROUP_BLOCK_PATTERNS):
            return False
        return True

    def _clean_context(self, lines, group: bool = False):
        """记忆上下文也要过边界：群友在聊天里塞「画裸体」不能顺着拼进提示词。"""
        return [x for x in (lines or []) if self._safe_text(x, group)]

    def _is_at_me(self, event, strict: bool = False) -> bool:
        """是否真的 @ 了机器人。

        strict=True 只认消息里的 At 组件，不认唤醒前缀——群里用唤醒词不算 @。
        """
        if not strict:
            try:
                if bool(getattr(event, "is_at_or_wake_command", False)):
                    return True
            except Exception:
                pass
        try:
            self_id = str(event.get_self_id() or "")
            message = getattr(getattr(event, "message_obj", None), "message", None) or []
            for comp in message:
                if type(comp).__name__ == "At" and str(getattr(comp, "qq", "")) == self_id and self_id:
                    return True
        except Exception:
            pass
        return False

    def _quota_ok(self, uid: str, umo: str = "", group: bool = False) -> tuple:
        """冷却 + 每日次数。私聊默认不限量，群聊按群每天 20 张。"""
        now = time.time()
        cooldown = max(0, self._int("cooldown_seconds", 30))
        last = self._last_call.get(uid, 0)
        if cooldown and last and now - last < cooldown:
            return (False, "刚画过，等我缓一下")
        today = time.strftime("%Y-%m-%d")
        if group:
            limit = max(0, self._int("group_daily_limit", 20))
            key = str(umo or "")
            if limit and key:
                record = self._daily_group.get(key) or [today, 0]
                if record[0] != today:
                    record = [today, 0]
                if record[1] >= limit:
                    self._daily_group[key] = record
                    return (False, "这个群今天画得够多啦，明天再画")
                self._daily_group[key] = record
            return (True, "")
        limit = max(0, self._int("private_daily_limit", 0))
        if limit:
            record = self._daily.get(uid) or [today, 0]
            if record[0] != today:
                record = [today, 0]
            if record[1] >= limit:
                self._daily[uid] = record
                return (False, "今天画得够多啦，明天再画")
            self._daily[uid] = record
        return (True, "")

    def _mark_call(self, uid: str, umo: str = "", group: bool = False, cooldown: bool = True):
        """记一次出图。cooldown=False 用于陪伴主动分享：算额度，但不占用户的冷却。"""
        if cooldown:
            self._last_call[uid] = time.time()
        today = time.strftime("%Y-%m-%d")
        record = self._daily.get(uid) or [today, 0]
        if record[0] != today:
            record = [today, 0]
        record[1] = int(record[1]) + 1
        self._daily[uid] = record
        if group:
            key = str(umo or "")
            if key:
                grecord = self._daily_group.get(key) or [today, 0]
                if grecord[0] != today:
                    grecord = [today, 0]
                grecord[1] = int(grecord[1]) + 1
                self._daily_group[key] = grecord

    # ---------- 后端 ----------

    def _endpoint(self) -> str:
        base = str(self._cfg("api_base", "") or "").strip().rstrip("/")
        if not base:
            raise ValueError("没配置 api_base")
        return base + "/images/generations"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        key = str(self._cfg("api_key", "") or "").strip()
        if key:
            headers["Authorization"] = "Bearer " + key
        return headers

    def _negative(self) -> str:
        value = self._cfg("negative_prompt", DEFAULT_NEGATIVE_PROMPT)
        if value is None:
            value = DEFAULT_NEGATIVE_PROMPT
        return str(value).strip()

    def _body(self, prompt: str, style: str = "openai", negative: str = "") -> dict:
        """请求体。不认厂商，只按「参数名风格」组装。

        openai：size / n（+ quality / style）
        aliases：image_size / batch_size —— 有些兼容层只认这两个名字
        negative：negative_prompt，后端认识就传，不认识由 `_generate` 去掉重试
        """
        body = {"prompt": prompt}
        if negative:
            body["negative_prompt"] = negative
        model = str(self._cfg("model", "") or "").strip()
        if model:
            body["model"] = model
        size = str(self._cfg("size", "1024x1024"))
        count = max(1, self._int("n", 1))
        if style == "aliases":
            body["image_size"] = size
            body["batch_size"] = count
            return body
        body["size"] = size
        body["n"] = count
        quality = str(self._cfg("quality", "") or "").strip()
        if quality:
            body["quality"] = quality
        style_name = str(self._cfg("style", "") or "").strip()
        if style_name:
            body["style"] = style_name
        return body

    async def _post_json(self, url: str, body: dict) -> dict:
        timeout = max(10.0, self._float("timeout", 90.0))
        raw = json.dumps(body).encode("utf-8")
        if aiohttp is not None:
            client_timeout = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(url, data=raw, headers=self._headers()) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        raise APIError(resp.status, text[:300])
                    return json.loads(text)
        return await asyncio.to_thread(self._post_json_blocking, url, raw, timeout)

    def _post_json_blocking(self, url: str, raw: bytes, timeout: float) -> dict:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, data=raw, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            raise APIError(exc.code, detail) from exc

    async def _download(self, url: str) -> bytes:
        timeout = max(10.0, self._float("timeout", 90.0))
        if aiohttp is not None:
            client_timeout = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.get(url) as resp:
                    if resp.status >= 400:
                        raise RuntimeError("下载图片 %s: %s" % (resp.status, await resp.text()))
                    return await resp.read()
        return await asyncio.to_thread(self._download_blocking, url, timeout)

    def _download_blocking(self, url: str, timeout: float) -> bytes:
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": "amo-image"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()

    RETRY_STATUS = (400, 404, 415, 422)

    def _style_order(self):
        """参数风格顺序：配置指定就用指定的；auto 时先试上次成功的，再换另一种。"""
        configured = str(self._cfg("request_style", "auto") or "auto").strip().lower()
        if configured in ("openai", "aliases"):
            return [configured]
        cached = self._style_cache.get(self._endpoint(), "")
        order = [cached] if cached in ("openai", "aliases") else []
        for item in ("openai", "aliases"):
            if item not in order:
                order.append(item)
        return order

    async def _generate(self, prompt: str) -> bytes:
        endpoint = self._endpoint()
        errors = []
        # 只有上次被后端明确拒过，才不再带 negative_prompt，省一次 400
        negative = self._negative() if self._negative_ok.get(endpoint, True) else ""
        for style in self._style_order():
            variants = [self._body(prompt, style, negative)]
            if negative:
                variants.append(self._body(prompt, style, ""))
            for index, body in enumerate(variants):
                try:
                    payload = await self._post_json(endpoint, body)
                except APIError as exc:
                    errors.append(str(exc))
                    if index == 0 and negative:
                        # 这个后端不认 negative_prompt：记住，并去掉它再试
                        self._negative_ok[endpoint] = False
                    # 只有「参数不认识 / 路径不对」才值得换一套参数重试；
                    # 401、429、5xx 换参数也没用，直接抛给上层。
                    if exc.status in self.RETRY_STATUS:
                        continue
                    raise
                self._style_cache[endpoint] = style
                if negative:
                    # 带 negative 的那一版成功才算认；去掉之后才成功说明不认
                    self._negative_ok[endpoint] = index == 0
                kind, value = parse_image_response(payload)
                return await self._download(value) if kind == "url" else value
        raise RuntimeError("；".join(errors[-2:]) or "生图接口没有可用的参数风格")

    # ---------- 一致性：同一个人、同一天同一套造型、同一个地点 ----------

    def _companion_state(self):
        """从陪伴读她的运行状态（此刻在干嘛、今天的日程）；读不到就返回空。"""
        companion = self._companion()
        if companion is None:
            return {}
        try:
            state = getattr(companion, "state", None)
            if state is None:
                return {}
            snapshot = {}
            try:
                snapshot = state.snapshot() or {}
            except Exception:
                snapshot = {}
            activity = {}
            try:
                activity = state.current_activity() or {}
            except Exception:
                activity = {}
            schedule = {}
            try:
                schedule = getattr(state, "schedule", {}) or {}
            except Exception:
                schedule = {}
            return {"snapshot": snapshot, "activity": activity, "schedule": schedule}
        except Exception:
            return {}

    def _outfits(self):
        value = self._cfg("character_outfits", [])
        if isinstance(value, str):
            value = [line.strip() for line in value.split("\n")]
        return [str(item).strip() for item in (value or []) if str(item).strip()]

    def _outfit_pool(self):
        """服装候选：没配就用内置保底，绝不让提示词里缺服装。"""
        return self._outfits() or list(DEFAULT_OUTFITS)

    def _outfit_cooldown(self):
        return max(0, self._int("outfit_change_cooldown_minutes", 60))

    def _outfit_change_words(self):
        value = self._cfg("outfit_change_keywords", ["换衣服", "换装", "洗澡", "换上", "换一身"])
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",")]
        return [str(item).strip() for item in (value or []) if str(item).strip()]

    # ---------- 手动换装（/换一套衣服）----------

    def _look_state_path(self):
        return os.path.join(self.plugin_dir, "look_state.json")

    def _load_look_state(self):
        if isinstance(self._look_state, dict):
            return self._look_state
        state = {}
        try:
            with open(self._look_state_path(), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                state = data
        except Exception:
            state = {}
        self._look_state = state
        return state

    def _look_bonus(self, day):
        """手动换装累计次数：只对当天有效，换了天自动归零。"""
        state = self._load_look_state()
        if str(state.get("date") or "") != str(day or ""):
            return 0
        try:
            return max(0, int(state.get("bonus") or 0))
        except Exception:
            return 0

    def _bump_look(self, day):
        """手动换下一套，返回新的累计次数；落盘失败也只影响重启后丢失。"""
        state = self._load_look_state()
        bonus = self._look_bonus(day) + 1
        self._look_state = {"date": str(day or ""), "bonus": bonus}
        try:
            path = self._look_state_path()
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(self._look_state, handle, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception as exc:
            logger.debug("[amo_image] 换装记录落盘失败: %s" % exc)
        return bonus

    def _today(self):
        day = str((self._companion_state().get("snapshot") or {}).get("date") or "").strip()
        return day or time.strftime("%Y-%m-%d")

    def _wake_prefixes(self):
        """AstrBot 的唤醒词；消息里带着它就剥掉，再判断是不是换装指令。"""
        try:
            config = self.context.get_config()
            prefixes = config.get("wake_prefix") or []
        except Exception:
            return []
        if isinstance(prefixes, str):
            prefixes = [prefixes]
        return [str(item) for item in prefixes if str(item)]

    def _outfit_request(self, text):
        """整句就是「换一套衣服」才算，别把聊天里提到换衣服当成指令。"""
        body = str(text or "").strip().strip(_TRIM_CHARS)
        if not body:
            return ""
        for prefix in self._wake_prefixes():
            if body.startswith(prefix):
                body = body[len(prefix):].strip().strip(_TRIM_CHARS)
                break
        for word in OUTFIT_COMMANDS:
            if body == word:
                return word
        return ""

    def _change_outfit(self):
        """手动换下一套，返回新的造型描述。"""
        day = self._today()
        self._bump_look(day)
        return self._daily_look()

    @staticmethod
    def _minutes_of(text):
        try:
            hour, minute = str(text or "").split(":")[:2]
            return int(hour) * 60 + int(minute)
        except Exception:
            return -1

    def _outfit_changes(self, day, now_minutes):
        """今天已经生效的换装次数。

        - 同一段日程只算一次：两次换装至少隔 `outfit_change_cooldown_minutes`，
          避免「换裙子」「换外套」连着两条把造型连跳好几套；
        - 只算已经开始的事件，没到点的不算。
        """
        words = self._outfit_change_words()
        gap = self._outfit_cooldown()          # 分钟
        schedule = (self._companion_state().get("schedule") or {}).get(day) or []
        starts = []
        for item in schedule:
            activity = str(item.get("activity") or "")
            if not any(word in activity for word in words):
                continue
            start = self._minutes_of(item.get("start"))
            if start < 0:
                start = 0          # 没写时间的换装：当天一早就算发生
            if start > now_minutes:
                continue
            starts.append(start)
        starts.sort()
        accepted = []
        for start in starts:
            if accepted and start - accepted[-1] < gap:
                continue
            accepted.append(start)
        return len(accepted)

    def _daily_look(self):
        """今天穿什么、头发什么样。

        - 服装池按「日期 + 今天已生效的换装次数 + 手动换装次数」取一套，
          同一天结果稳定，重启也不变；
        - 日程里出现「换衣服/洗澡」这类词，且间隔够久，才会换下一套；
        - 服装池留空时用内置保底池，保证提示词里永远有服装描述。
        """
        hairstyle = str(self._cfg("character_hairstyle", "") or "").strip()
        outfits = self._outfit_pool()
        state = self._companion_state()
        snapshot = state.get("snapshot") or {}
        day = str(snapshot.get("date") or "").strip()
        if not day:
            day = time.strftime("%Y-%m-%d")
        now_minutes = self._minutes_of(snapshot.get("time") or "")
        if now_minutes < 0:
            now = time.localtime()
            now_minutes = now.tm_hour * 60 + now.tm_min
        changes = self._outfit_changes(day, now_minutes) + self._look_bonus(day)
        index = (zlib.crc32(day.encode("utf-8")) + changes) % len(outfits)
        outfit = outfits[index]
        if hairstyle:
            return "穿%s，%s" % (outfit, hairstyle)
        return "穿%s" % outfit

    def _scene_place(self):
        """此刻她在哪。锁定日程时，就在日程那一格的地点，不随机换场景。"""
        if not self._bool("lock_scene_to_schedule", True):
            return ""
        state = self._companion_state()
        activity = state.get("activity") or {}
        return str(activity.get("place") or "").strip()

    def _final_prompt(self, prompt, context_lines=None):
        return build_prompt(
            self._cfg("character_appearance", ""),
            prompt,
            context_lines,
            look=self._daily_look(),
            place=self._scene_place(),
        )

    async def draw_for(self, umo: str, uid: str = "", prompt: str = "") -> bytes | None:
        """给别的插件（陪伴）用的公开出图接口：不接事件，直接返回图片字节。

        失败/关闭/空提示词一律返回 None，绝不抛异常——调用方按「没画出来」处理即可。
        """
        if not self._bool("enable", True):
            return None
        prompt = str(prompt or "").strip()
        if not prompt:
            return None
        group = self._group_from_umo(umo)
        if not self._safe_text(prompt, group):
            logger.info("[amo_image] draw_for 输入被内容边界拦下")
            return None
        ok, note = self._quota_ok(str(uid or ""), str(umo or ""), group)
        if not ok:
            logger.info("[amo_image] draw_for 跳过：%s" % (note or "quota"))
            return None
        try:
            context_lines = self._clean_context(
                await self._context_lines(str(umo or ""), str(uid or "")), group
            )
            final_prompt = self._final_prompt(prompt, context_lines)
            data = await self._generate(final_prompt)
        except Exception as exc:
            logger.warning("[amo_image] draw_for 失败: %s" % exc)
            return None
        if data:
            # 主动分享算群额度，但不占用户的下一次冷却
            self._mark_call(str(uid or ""), str(umo or ""), group, cooldown=False)
            self._record(uid or "share", prompt, True, 0, len(data))
        return data or None

    # ---------- 面板 Web API ----------

    def _register_web_apis(self):
        """把面板接口挂到 /Astrbot_Amo_Image/*；注册失败只记日志，不影响生图。"""
        routes = [
            ("/status", self.api_status, ["GET"]),
            ("/history", self.api_history, ["GET"]),
            ("/config", self.api_config, ["GET"]),
            ("/test", self.api_test, ["POST"]),
        ]
        for path, handler, methods in routes:
            try:
                self.context.register_web_api("/" + PLUGIN_NAME + path, handler, methods, path)
            except TypeError:
                try:
                    self.context.register_web_api("/" + PLUGIN_NAME + path, methods, path, handler)
                except Exception as exc:
                    logger.warning("[amo_image] 面板接口 %s 兼容注册失败: %s" % (path, exc))
            except Exception as exc:
                logger.warning("[amo_image] 面板接口 %s 注册失败: %s" % (path, exc))

    @staticmethod
    def _json(payload):
        if json_response is not None:
            try:
                return json_response(payload)
            except Exception:
                pass
        return payload

    @staticmethod
    def _fail(message):
        if error_response is not None:
            try:
                return error_response(message)
            except Exception:
                pass
        return {"code": 1, "message": message}

    def _mask_key(self) -> str:
        key = str(self._cfg("api_key", "") or "")
        if not key:
            return ""
        if len(key) <= 10:
            return "已配置"
        return key[:4] + "…" + key[-4:]

    def _today_used(self) -> int:
        today = time.strftime("%Y-%m-%d")
        total = 0
        for record in self._daily.values():
            if isinstance(record, list) and len(record) == 2 and record[0] == today:
                total += int(record[1])
        return total

    def _today_group_used(self) -> int:
        today = time.strftime("%Y-%m-%d")
        total = 0
        for record in self._daily_group.values():
            if isinstance(record, list) and len(record) == 2 and record[0] == today:
                total += int(record[1])
        return total

    async def api_status(self):
        return self._json({
            "code": 0,
            "data": {
                "name": PLUGIN_NAME,
                "version": PLUGIN_VERSION,
                "enabled": self._bool("enable", True),
                "api_base": str(self._cfg("api_base", "") or ""),
                "api_key_masked": self._mask_key(),
                "model": str(self._cfg("model", "") or ""),
                "size": str(self._cfg("size", "") or ""),
                "allow_private": self._bool("allow_private", True),
                "allow_group": self._bool("allow_group", True),
                "cooldown_seconds": self._int("cooldown_seconds", 30),
                "private_daily_limit": self._int("private_daily_limit", 0),
                "group_daily_limit": self._int("group_daily_limit", 20),
                "safe_mode": self._bool("safe_mode", True),
                "users_today": self._today_used(),
                "groups_today": self._today_group_used(),
                "calls_total": self._calls_total,
                "history_size": len(self._history),
                "look": self._daily_look(),
                "uptime_seconds": int(time.time() - self._started_at),
            },
        })

    async def api_history(self):
        return self._json({"code": 0, "data": {"items": list(self._history)}})

    async def api_config(self):
        keys = [
            "enable", "api_base", "model", "size", "n", "quality", "style", "timeout",
            "character_appearance", "use_memory_context", "memory_context_limit",
            "allow_private", "allow_group", "require_at_in_group", "quote_in_group",
            "keywords", "cooldown_seconds", "private_daily_limit", "group_daily_limit",
            "safe_mode", "blocked_reply", "reply_prefix", "error_reply",
            "character_hairstyle", "character_outfits", "outfit_change_keywords",
            "outfit_change_cooldown_minutes", "lock_scene_to_schedule", "negative_prompt",
        ]
        data = {k: self._cfg(k, None) for k in keys}
        data["api_key"] = self._mask_key()
        return self._json({"code": 0, "data": data})

    async def api_test(self):
        """面板里试画一张：返回 data:image/png;base64，前端直接 <img>。"""
        payload = {}
        if web_request is not None:
            try:
                payload = await web_request.json(default={})
            except Exception:
                payload = {}
        prompt = str((payload or {}).get("prompt") or "").strip() or "窗边的猫，午后阳光"
        final_prompt = self._final_prompt(prompt, [])
        started = time.time()
        try:
            data = await self._generate(final_prompt)
        except Exception as exc:
            self._record("panel", prompt, False, int((time.time() - started) * 1000), 0)
            return self._fail("生图失败：%s" % exc)
        if not data:
            self._record("panel", prompt, False, int((time.time() - started) * 1000), 0)
            return self._fail("后端返回空")
        self._record("panel", prompt, True, int((time.time() - started) * 1000), len(data))
        return self._json({
            "code": 0,
            "data": {
                "image": "data:image/png;base64," + base64.b64encode(data).decode(),
                "bytes": len(data),
                "ms": int((time.time() - started) * 1000),
                "prompt": final_prompt,
            },
        })

    def _record(self, uid, prompt, ok, ms, size):
        self._calls_total += 1
        try:
            self._history.append({
                "ts": int(time.time()),
                "time": time.strftime("%m-%d %H:%M:%S"),
                "uid": str(uid or ""),
                "prompt": str(prompt or "")[:80],
                "ok": bool(ok),
                "ms": int(ms),
                "bytes": int(size),
            })
        except Exception:
            pass

    # ---------- 发送 ----------

    def _result(self, event, data: bytes, prefix: str = ""):
        """优先 chain_result（一句话 + 图），失败退回临时文件 + image_result。"""
        text = str(prefix or "").strip()
        try:
            chain = []
            if self._bool("quote_in_group", True) and self._is_group(event):
                mid = getattr(getattr(event, "message_obj", None), "message_id", None)
                if mid:
                    chain.append(Reply(id=mid))
            if text:
                chain.append(Plain(text=text))
            chain.append(Image.fromBytes(data))
            return event.chain_result(chain)
        except Exception as exc:
            logger.debug("[amo_image] chain_result 不可用，改用临时文件: %s" % exc)
        handle, path = tempfile.mkstemp(prefix="amo_image_", suffix=".png")
        try:
            os.write(handle, data)
        finally:
            os.close(handle)
        return event.image_result(path)

    # ---------- 主链路 ----------

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def on_message(self, event: AstrMessageEvent):
        if not self._bool("enable", True):
            return
        try:
            text = str(getattr(event, "message_str", "") or "").strip()
            if not text:
                return
            group = self._is_group(event)
            if group and not self._bool("allow_group", True):
                return
            if (not group) and not self._bool("allow_private", True):
                return
            if group and self._bool("require_at_in_group", True) and not self._is_at_me(event, strict=True):
                return
            if self._outfit_request(text):
                try:
                    look = self._change_outfit()
                    yield event.plain_result("换好了：%s" % look)
                except Exception as exc:
                    logger.debug("[amo_image] 手动换装失败: %s" % exc)
                event.stop_event()
                return
            photo_words = self._photo_keywords()
            keyword = match_keyword(text, self._keywords() + photo_words)
            if not keyword:
                return
            prompt = strip_filler(clean_prompt(text, keyword))
            if len(prompt) < 2:
                # 「给我拍张照」这类没有具体内容，用默认自拍提示词；其它情况才反问
                if keyword in photo_words:
                    prompt = str(self._cfg("photo_default_prompt",
                                           "自拍，日常光线，自然表情，看向镜头"))
                else:
                    yield event.plain_result("想画什么？说具体点")
                    event.stop_event()
                    return
            if not self._safe_text(prompt, group):
                yield event.plain_result(str(self._cfg("blocked_reply", "这个画不了，换个描述吧")))
                event.stop_event()
                return
            async for result in self._draw(event, prompt):
                yield result
            event.stop_event()
        except Exception as exc:  # 兜底：绝不把异常抛回框架
            logger.warning("[amo_image] 处理消息失败: %s" % exc)

    async def _draw(self, event, prompt: str):
        uid = str(getattr(event, "get_sender_id", lambda: "")() or "")
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        group = self._is_group(event)
        if not self._safe_text(prompt, group):
            yield event.plain_result(str(self._cfg("blocked_reply", "这个画不了，换个描述吧")))
            return
        ok, note = self._quota_ok(uid, umo, group)
        if not ok:
            yield event.plain_result(note or str(self._cfg("error_reply", "画不出来，等会儿再试试")))
            return
        ctx = self._clean_context(await self._context_lines(umo, uid), group)
        final_prompt = self._final_prompt(prompt, ctx)
        self._mark_call(uid, umo, group)
        started = time.time()
        try:
            data = await self._generate(final_prompt)
        except Exception as exc:
            logger.warning("[amo_image] 生图失败: %s" % exc)
            self._record(uid, prompt, False, int((time.time() - started) * 1000), 0)
            yield event.plain_result(str(self._cfg("error_reply", "画不出来，等会儿再试试")))
            return
        if not data:
            logger.warning("[amo_image] 生图返回空")
            self._record(uid, prompt, False, int((time.time() - started) * 1000), 0)
            yield event.plain_result(str(self._cfg("error_reply", "画不出来，等会儿再试试")))
            return
        self._record(uid, prompt, True, int((time.time() - started) * 1000), len(data))
        yield self._result(event, data, str(self._cfg("reply_prefix", "") or ""))

    # ---------- 命令 ----------

    @filter.command("amoimage")
    async def amoimage(self, event: AstrMessageEvent, *args):
        """生图插件的状态与手动操作：/amoimage status | draw <提示词> | outfit"""
        args = [str(x) for x in (args or []) if str(x).strip()]
        action = args[0].lower() if args else "status"
        if action in ("status", "状态"):
            uid = str(getattr(event, "get_sender_id", lambda: "")() or "")
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            today = time.strftime("%Y-%m-%d")
            record = self._daily.get(uid) or [today, 0]
            used = record[1] if record[0] == today else 0
            private_limit = self._int("private_daily_limit", 0)
            group_record = self._daily_group.get(umo) or [today, 0]
            group_used = group_record[1] if group_record[0] == today else 0
            group_limit = self._int("group_daily_limit", 20)
            yield event.plain_result(
                "生图：%s\n后端：%s\n模型：%s\n私聊今日：%s/%s\n本群今日：%s/%s\n今天穿：%s" % (
                    "开" if self._bool("enable", True) else "关",
                    str(self._cfg("api_base", "") or "未配置"),
                    str(self._cfg("model", "") or "-"),
                    used,
                    private_limit if private_limit else "不限",
                    group_used,
                    group_limit if group_limit else "不限",
                    self._daily_look(),
                )
            )
            return
        if action in ("outfit", "换衣服", "换装"):
            # 手动换下一套，不受日程和冷却限制
            yield event.plain_result("换好了：%s" % self._change_outfit())
            return
        if action in ("draw", "画"):
            prompt = " ".join(args[1:]).strip()
            if not prompt:
                yield event.plain_result("用法：/amoimage draw <提示词>")
                return
            async for result in self._draw(event, prompt):
                yield result
            return
        yield event.plain_result("用法：/amoimage status ｜ draw <提示词> ｜ outfit")
