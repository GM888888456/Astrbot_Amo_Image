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

try:
    from .providers import (
        APIError,
        ImageOptions,
        build_provider,
        parse_image_response,
        ratio_from_size,
    )
except Exception:  # pragma: no cover - 直接以脚本方式加载时
    from providers import (  # type: ignore
        APIError,
        ImageOptions,
        build_provider,
        parse_image_response,
        ratio_from_size,
    )

PLUGIN_NAME = "Astrbot_Amo_Image"
PLUGIN_VERSION = "1.6.1"
try:  # 版本号以 metadata.yaml 为准，避免两处不一致
    import yaml as _amo_meta_yaml
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "metadata.yaml"),
              encoding="utf-8") as _amo_meta_fh:
        _amo_meta = _amo_meta_yaml.safe_load(_amo_meta_fh) or {}
    if _amo_meta.get("version"):
        PLUGIN_VERSION = str(_amo_meta["version"])
except Exception:
    pass

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

# 拍摄角度：拍自己=自拍（前置、第一人称）；拍别人/别的东西=他拍（第三人称，别人拿手机）
SELFIE_CAMERA = "手机前置摄像头，第一人称视角，靠近镜头"
OTHER_CAMERA = "他拍，第三人称视角，对面的人拿手机拍，画面里不要出现拿手机的人"
SELFIE_WORDS = ("自拍", "拍你", "拍我", "看看你", "你的照片", "自拍照", "前置",
                "镜头对着自己", "来张自拍", "发张自拍")
OTHER_WORDS = ("他", "她", "朋友", "猫", "狗", "宠物", "一起", "合影", "全身", "背影",
               "桌上", "风景", "街", "店", "咖啡", "蛋糕", "花", "书", "电脑", "相机",
               "天空", "海", "山", "路", "窗外", "雨", "食物", "菜", "画")

# 没配外观时的保底描述：宁可普通，也不能每次换一张脸
DEFAULT_APPEARANCE = "长发，五官清秀，自然妆容，日常穿搭"

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

# 面板里的后端预设：只填 base/model，密钥由用户自己填
PROVIDER_PRESETS = (
    {"id": "openai", "name": "OpenAI 官方", "provider": "openai",
     "api_base": "https://api.openai.com/v1", "model": "gpt-image-1"},
    {"id": "siliconflow", "name": "硅基流动", "provider": "openai",
     "api_base": "https://api.siliconflow.cn/v1", "model": "Kwai-Kolors/Kolors"},
    {"id": "zhipu", "name": "智谱 BigModel", "provider": "openai",
     "api_base": "https://open.bigmodel.cn/api/paas/v4", "model": "cogview-3-flash"},
    {"id": "dashscope", "name": "阿里百炼", "provider": "openai",
     "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "wanx2.1-t2i-turbo"},
    {"id": "gemini", "name": "Gemini 官方", "provider": "gemini",
     "api_base": "https://generativelanguage.googleapis.com/v1beta",
     "model": "gemini-2.5-flash-image"},
    {"id": "chat", "name": "Chat 通道（gpt-image / 代理）", "provider": "openai_chat",
     "api_base": "https://api.openai.com/v1", "model": "gpt-4o-image"},
    {"id": "custom", "name": "自定义端点", "provider": "custom",
     "api_base": "", "model": ""},
)

# 面板能改的配置项（其余走 AstrBot 自己的配置页）
WEB_EDITABLE = (
    "provider", "api_base", "api_key", "model", "size", "n", "quality", "style",
    "timeout", "request_style", "negative_prompt", "aspect_ratio", "chat_modalities",
    "custom_url", "custom_body", "custom_response_path",
    "enable", "allow_private", "allow_group", "require_at_in_group", "quote_in_group",
    "cooldown_seconds", "private_daily_limit", "group_daily_limit", "safe_mode",
    "blocked_reply", "reply_prefix", "error_reply",
    "character_appearance", "character_hairstyle", "character_outfits",
    "character_gender", "character_persona", "camera_mode",
    "outfit_change_keywords", "outfit_change_cooldown_minutes",
    "lock_scene_to_schedule", "keywords", "photo_keywords", "photo_default_prompt",
    "use_memory_context", "memory_context_limit",
)
WEB_BOOL_KEYS = frozenset({
    "enable", "allow_private", "allow_group", "require_at_in_group", "quote_in_group",
    "safe_mode", "lock_scene_to_schedule", "use_memory_context", "chat_modalities",
})
WEB_INT_KEYS = frozenset({
    "n", "timeout", "cooldown_seconds", "private_daily_limit", "group_daily_limit",
    "memory_context_limit", "outfit_change_cooldown_minutes",
})
WEB_LIST_KEYS = frozenset({"character_outfits", "outfit_change_keywords", "keywords", "photo_keywords"})

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


def build_prompt(appearance: str, prompt: str, context_lines=None, look: str = "",
                 place: str = "", activity: str = "", gender: str = "",
                 persona: str = "", camera: str = "",
                 look_label: str = "今天的样子：") -> str:
    """拼最终提示词：性别 → 角色 → 外观 → 今日固定造型 → 地点/在干嘛 → 要求 → 上下文。

    性别和角色放在最前面，是因为模型默认会「猜」人物：不写性别就容易画出男的，
    不写角色名就接不上人格。地点和 activity 都来自日程里此刻那一格，不随机换。
    """
    parts = []
    body = str(prompt or "")
    if str(gender or "").strip():
        parts.append(str(gender).strip())
    if str(persona or "").strip() and str(persona).strip() not in body:
        parts.append("角色：" + str(persona).strip())
    if str(appearance or "").strip():
        parts.append(str(appearance).strip())
    # 陪伴那边的分享模板本身就带了 {place}，这里别再叠一遍「此刻在××」
    if str(look or "").strip() and str(look).strip() not in body:
        parts.append(str(look_label or "今天的样子：") + str(look).strip())
    if str(camera or "").strip():
        parts.append(str(camera).strip())
    where = str(place or "").strip()
    doing = str(activity or "").strip()
    if where and where not in body:
        if doing and doing not in body:
            parts.append("此刻在%s，正在%s" % (where, doing[:40]))
        else:
            parts.append("此刻在" + where)
    elif doing and doing not in body:
        parts.append("正在" + doing[:40])
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

    async def _post_json(self, url: str, body: dict, headers=None) -> dict:
        timeout = max(10.0, self._float("timeout", 90.0))
        raw = json.dumps(body).encode("utf-8")
        headers = dict(headers or self._headers())
        if aiohttp is not None:
            client_timeout = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(url, data=raw, headers=headers) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        raise APIError(resp.status, text[:300])
                    return json.loads(text)
        return await asyncio.to_thread(self._post_json_blocking, url, raw, timeout, headers)

    def _post_json_blocking(self, url: str, raw: bytes, timeout: float, headers=None) -> dict:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, data=raw, headers=dict(headers or self._headers()), method="POST")
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

    async def _get_json(self, url: str, headers=None):
        """GET 一个 JSON（拉模型列表用）；失败抛异常，由调用方转成提示。"""
        timeout = max(10.0, self._float("timeout", 90.0))
        headers = dict(headers or self._headers())
        if aiohttp is not None:
            client_timeout = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        raise APIError(resp.status, text[:300])
                    return json.loads(text)
        return await asyncio.to_thread(self._get_json_blocking, url, timeout, headers)

    def _get_json_blocking(self, url: str, timeout: float, headers=None):
        import urllib.request

        req = urllib.request.Request(url, headers=dict(headers or self._headers()))
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))

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

    def _provider(self):
        """按配置挑后端（openai / openai_chat / gemini / custom）。"""
        return build_provider(self)

    def _options(self):
        return ImageOptions(
            size=self._cfg("size", "1024x1024"),
            n=self._int("n", 1),
            quality=str(self._cfg("quality", "") or ""),
            style=str(self._cfg("style", "") or ""),
            negative=self._negative(),
            aspect_ratio=str(self._cfg("aspect_ratio", "") or ""),
        )

    async def _generate(self, prompt: str) -> bytes:
        return await self._provider().generate(prompt, self._options())

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

    def _plugin_data_dir(self):
        """上架规范：持久化数据放 data/plugin_data/<插件名>/，不落在插件安装目录。"""
        name = os.path.basename(self.plugin_dir) or "Astrbot_Amo_Image"
        for getter in ("get_plugin_data_dir", "plugin_data_dir"):
            try:
                value = getattr(self.context, getter, None)
                value = value() if callable(value) else value
                value = str(value or "").strip()
                if value:
                    return value
            except Exception:
                continue
        return os.path.join(os.path.dirname(self.plugin_dir), "plugin_data", name)

    def _look_state_path(self):
        root = self._plugin_data_dir()
        try:
            os.makedirs(root, exist_ok=True)
        except Exception:
            pass
        return os.path.join(root, "look_state.json")

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

    def _scene_activity(self):
        """此刻在干嘛；和地点一样来自日程，避免图和日程对不上。"""
        if not self._bool("lock_scene_to_schedule", True):
            return ""
        activity = (self._companion_state().get("activity") or {})
        return str(activity.get("activity") or "").strip()[:40]

    def _gender(self):
        """性别锚：不写的话模型会自由发挥，之前就出现过画成男生。

        `自动` 时先看外观/角色描述里有没有线索，没有就按 Amo 的默认女生。
        """
        raw = str(self._cfg("character_gender", "自动") or "自动").strip()
        if raw in ("女", "女生", "female", "f", "F"):
            return "女生"
        if raw in ("男", "男生", "male", "m", "M"):
            return "男生"
        text = " ".join([self._appearance(), str(self._cfg("character_persona", "") or "")])
        if any(word in text for word in ("男生", "男孩", "少年", "男性")):
            return "男生"
        return "女生"

    def _persona(self):
        """角色名/一句话人设；没配就用陪伴的人格名。"""
        value = str(self._cfg("character_persona", "") or "").strip()
        if value:
            return value
        companion = self._companion()
        name = str(getattr(companion, "persona_name", "") or "").strip()
        if not name:
            conf = getattr(companion, "config", None) or {}
            name = str(conf.get("persona_name") or conf.get("bot_name") or "").strip()
        return name

    def _camera(self, prompt):
        """拍摄角度：拍自己=自拍，拍别人/别的东西=他拍。

        这条不写，模型会把「桌上的一杯咖啡」也画成怼脸自拍，或者把「自拍」画成
        别人站在对面拍——角度错了比衣服错了还明显。
        """
        mode = str(self._cfg("camera_mode", "自动") or "自动").strip()
        if mode in ("自拍", "selfie"):
            return SELFIE_CAMERA
        if mode in ("他拍", "third", "第三人称"):
            return OTHER_CAMERA
        value = str(prompt or "")
        if any(word in value for word in SELFIE_WORDS):
            return SELFIE_CAMERA
        if any(word in value for word in OTHER_WORDS):
            return OTHER_CAMERA
        return SELFIE_CAMERA  # 没线索时按自拍：她要给人看的通常是自己的照片

    def _scheduled_outfit(self):
        """日程里这一段穿什么；陪伴没装/旧日程没有 outfit 时返回空。"""
        activity = (self._companion_state().get("activity") or {})
        return str(activity.get("outfit") or "").strip()

    def _appearance(self):
        """外观：没配就用保底描述，避免每次生成都换一张脸。"""
        value = str(self._cfg("character_appearance", "") or "").strip()
        return value or DEFAULT_APPEARANCE

    def _final_prompt(self, prompt, context_lines=None):
        scheduled = self._scheduled_outfit()
        return build_prompt(
            self._appearance(),
            prompt,
            context_lines,
            look=scheduled or self._daily_look(),
            look_label="此刻穿着：" if scheduled else "今天的样子：",
            place=self._scene_place(),
            activity=self._scene_activity(),
            gender=self._gender(),
            persona=self._persona(),
            camera=self._camera(prompt),
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
            ("/config/save", self.api_save_config, ["POST"]),
            ("/presets", self.api_presets, ["GET"]),
            ("/models", self.api_models, ["GET"]),
            ("/persona/extract", self.api_persona_extract, ["POST"]),
            ("/wardrobe", self.api_wardrobe, ["GET", "POST"]),
            ("/wardrobe/save", self.api_wardrobe_save, ["POST"]),
            ("/wardrobe/delete", self.api_wardrobe_delete, ["POST"]),
            ("/wardrobe/wear", self.api_wardrobe_wear, ["POST"]),
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
                "provider": self._provider().name,
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
                "look": self._scheduled_outfit() or self._daily_look(),
                "uptime_seconds": int(time.time() - self._started_at),
            },
        })

    async def api_history(self):
        return self._json({"code": 0, "data": {"items": list(self._history)}})

    async def api_config(self):
        keys = [
            "provider", "enable", "api_base", "model", "size", "n", "quality", "style",
            "timeout", "request_style", "negative_prompt", "aspect_ratio", "chat_modalities",
            "custom_url", "custom_body", "custom_response_path",
            "character_appearance", "use_memory_context", "memory_context_limit",
            "allow_private", "allow_group", "require_at_in_group", "quote_in_group",
            "keywords", "photo_keywords", "photo_default_prompt", "cooldown_seconds",
            "private_daily_limit", "group_daily_limit",
            "safe_mode", "blocked_reply", "reply_prefix", "error_reply",
            "character_hairstyle", "character_outfits", "character_gender",
            "character_persona", "camera_mode", "outfit_change_keywords",
            "outfit_change_cooldown_minutes", "lock_scene_to_schedule",
        ]
        data = {k: self._cfg(k, None) for k in keys}
        data["provider"] = str(self._cfg("provider", "openai") or "openai")
        data["character_gender"] = str(self._cfg("character_gender", "自动") or "自动")
        # 面板只回掩码；真密钥不经过前端，避免误存/误传
        data["api_key"] = ""
        data["api_key_masked"] = self._mask_key()
        return self._json({"code": 0, "data": data})

    @staticmethod
    def _collect_models(payload):
        """从各家 /models 响应里收集模型名：data[].id、models[].name、纯字符串数组。"""
        items = []
        if isinstance(payload, dict):
            for key in ("data", "models", "result", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    items = value
                    break
        elif isinstance(payload, list):
            items = payload
        names = []
        for item in items:
            if isinstance(item, str):
                name = item
            elif isinstance(item, dict):
                name = item.get("id") or item.get("name") or item.get("model") or ""
                if isinstance(name, str) and name.startswith("models/"):
                    name = name.split("/", 1)[1]
            else:
                continue
            name = str(name or "").strip()
            if name and name not in names:
                names.append(name)
        return names

    async def api_models(self):
        """拉当前后端的模型列表，面板里只让选、不让手填。"""
        provider = self._provider().name
        base = str(self._cfg("api_base", "") or "").strip().rstrip("/")
        key = str(self._cfg("api_key", "") or "").strip()
        if provider == "custom":
            return self._json({"code": 1, "message": "自定义后端没有模型列表，请在配置里手填 model"})
        if not base:
            return self._json({"code": 1, "message": "先填 api_base"})
        try:
            if provider == "gemini":
                url = base + "/models"
                headers = {"x-goog-api-key": key} if key else {}
                payload = await self._get_json(url, headers)
                names = [name for name in self._collect_models(payload) if "image" in name.lower()]
                if not names:
                    names = self._collect_models(payload)
            else:
                url = base + "/models"
                headers = self._headers()
                payload = await self._get_json(url, headers)
                names = self._collect_models(payload)
            current = str(self._cfg("model", "") or "").strip()
            if current and current not in names:
                names.insert(0, current)
            return self._json({"code": 0, "data": {
                "provider": provider, "models": names,
                "current": current, "count": len(names)}})
        except Exception as exc:
            logger.warning("[amo_image] 拉模型列表失败: %s" % exc)
            return self._json({"code": 1, "message": "拉取失败：%s" % exc})

    async def api_presets(self):
        """面板里的后端预设，只填 base/model，不动密钥。"""
        return self._json({"code": 0, "data": {"items": list(PROVIDER_PRESETS)}})

    # 外观线索词：命中这些词的分句才像「长什么样」，不是性格/说话风格
    _LOOK_WORDS = (
        "头发", "长发", "短发", "发色", "蓝发", "银发", "黑发", "金发",
        "眼睛", "眼眸", "瞳孔", "耳朵", "兽耳", "鳍耳", "尾巴",
        "皮肤", "身高", "体型", "脸型", "酒窝", "虎牙", "刘海",
    )
    _LOOK_KEYS = (
        "外貌", "长相", "样子", "发色", "发型", "瞳色", "眼睛",
        "体型", "体型特征", "特征", "穿着", "服装", "皮肤",
        # 身高体重年龄也算人物设定的一部分：模型不看数字，但「25岁 / 155cm / 娇小」
        # 这类描述能帮它定住年龄感和体型，别每次画成不同的人
        "年龄", "身高", "身高体重", "体重",
    )
    # 这些词说明是行为/性格，不是长相
    _BEHAVIOR_WORDS = (
        "会", "喜欢", "被", "抱", "亲", "撒娇", "怼", "回", "说",
        "觉得", "认为", "习惯", "总", "得意", "开心", "生气", "委屈",
        "哭", "笑", "叫", "喊", "缠", "粘",
    )
    _MINOR_WORDS = ("萝莉", "loli", "幼女", "未成年", "正太")

    @classmethod
    def _look_clauses(cls, text):
        """从人格文本里挑出描述长相的分句；挑不到就返回空。

        人格常是「姓名：… / 体型特征：… / 爱好：…」这种条目，优先读长相相关的 key；
        读不到再扫分句，但要把「被夸了会翘尾巴」这种行为描写排除掉。
        """
        out = []

        def take(clause):
            clause = clause.strip()
            if not clause or len(clause) > 24:
                return
            if any(word in clause for word in cls._BEHAVIOR_WORDS):
                return
            if any(word in clause for word in cls._MINOR_WORDS):
                return
            clause = clause.replace(" / ", "，").replace("/", "，").strip("， ")
            if clause and clause not in out:
                out.append(clause)

        for line in str(text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            key, value = "", line
            for sep in ("：", ":"):
                if sep in line:
                    key, value = [part.strip() for part in line.split(sep, 1)]
                    break
            clauses = [part.strip() for part in re.split(r"[，,。；;]", value) if part.strip()]
            if key in cls._LOOK_KEYS:
                for clause in clauses:
                    take(clause)
                    if len(out) >= 3:
                        return out
                continue
            for clause in clauses:
                if any(word in clause for word in cls._LOOK_WORDS):
                    take(clause)
                    if len(out) >= 3:
                        return out
        return out

    @staticmethod
    def _guess_gender(text):
        raw = str(text or "")
        female = sum(raw.count(word) for word in ("女孩", "女生", "少女", "女性", "姑娘", "妻子", "老婆", "她"))
        male = sum(raw.count(word) for word in ("男孩", "男生", "少年", "男性"))
        return "男" if male > female else "女"

    async def _persona_text(self):
        """读陪伴的人格设定；读不到返回空串。"""
        companion = self._companion()
        llm = getattr(companion, "llm", None)
        reader = getattr(llm, "persona", None) if llm is not None else None
        if callable(reader):
            try:
                text = await reader("")
                if text:
                    return str(text)
            except Exception as exc:
                logger.debug("[amo_image] 读人格失败: %s" % exc)
        try:
            manager = getattr(self.context, "persona_manager", None)
            if manager is not None:
                persona = await manager.get_default_persona_v3(None)
                if isinstance(persona, dict):
                    return str(persona.get("prompt") or "")
                return str(getattr(persona, "prompt", "") or "")
        except Exception as exc:
            logger.debug("[amo_image] 读 AstrBot 人格失败: %s" % exc)
        return ""

    def _persona_name(self):
        companion = self._companion()
        name = str(getattr(companion, "persona_name", "") or "").strip()
        if not name:
            conf = getattr(companion, "config", None) or {}
            name = str(conf.get("persona_name") or conf.get("bot_name") or "").strip()
        return name

    # ---------- 服装库（数据在陪伴插件，这里只做面板代理）----------

    async def _companion_call(self, name, payload=None):
        """调陪伴插件上的同名接口；没装就直接说清楚。"""
        companion = self._companion()
        handler = getattr(companion, name, None) if companion is not None else None
        if not callable(handler):
            return self._fail("没装陪伴插件（Astrbot_Amo_forever），服装库在它那边")
        try:
            result = await handler()
        except Exception as exc:
            logger.warning("[amo_image] 转发 %s 失败: %s" % (name, exc))
            return self._fail("陪伴插件处理失败：%s" % exc)
        return result if isinstance(result, (dict, list)) else self._fail("陪伴插件返回异常")

    def _wardrobe_payload(self, payload):
        """面板已经把 body 交给陪伴的 _body()，这里只做兜底。"""
        return payload if isinstance(payload, dict) else {}

    async def api_wardrobe(self):
        return await self._companion_call("api_wardrobe")

    async def api_wardrobe_save(self):
        return await self._companion_call("api_wardrobe_save")

    async def api_wardrobe_delete(self):
        return await self._companion_call("api_wardrobe_delete")

    async def api_wardrobe_wear(self):
        return await self._companion_call("api_wardrobe_wear")

    async def api_persona_extract(self):
        """从人格设定里提取性别/外观/角色名，填进面板。"""
        text = await self._persona_text()
        clauses = self._look_clauses(text)
        appearance = "，".join(clauses)[:160]
        source = "人格"
        if not text:
            source = "默认"
        return self._json({
            "code": 0,
            "data": {
                "gender": self._guess_gender(text),
                "appearance": appearance,
                "persona": self._persona_name(),
                "source": source,
                "raw": str(text or "").strip()[:160],
            },
        })

    def _to_bool(self, value):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on", "是", "开")

    async def api_save_config(self):
        """面板保存配置：只认白名单字段，密钥留空/掩码不覆盖。"""
        payload = {}
        if web_request is not None:
            try:
                payload = await web_request.json(default={}) or {}
            except Exception:
                payload = {}
        if not isinstance(payload, dict):
            return self._fail("配置格式不对")
        data = {}
        for key in WEB_EDITABLE:
            if key not in payload:
                continue
            value = payload[key]
            try:
                if key in WEB_BOOL_KEYS:
                    value = self._to_bool(value)
                elif key in WEB_INT_KEYS:
                    value = int(float(value))
                elif key in WEB_LIST_KEYS:
                    if isinstance(value, str):
                        text = value.replace("，", "\n").replace(",", "\n")
                        value = [line.strip() for line in text.splitlines()]
                    value = [str(item).strip() for item in (value or []) if str(item).strip()]
                else:
                    value = str("" if value is None else value)
            except Exception:
                continue
            data[key] = value
        # 密钥：留空 / 回传掩码都不覆盖
        key_value = str(data.get("api_key", "") or "")
        if not key_value or "…" in key_value or key_value == self._mask_key():
            data.pop("api_key", None)
        if not data:
            return self._fail("没有要保存的字段")
        try:
            if hasattr(self.config, "update"):
                self.config.update(data)
            else:
                self.config = dict(self.config or {})
                self.config.update(data)
        except Exception as exc:
            return self._fail("保存失败：%s" % exc)
        # 换后端/地址/模型后，旧的参数风格结论不再作数
        if any(name in data for name in ("provider", "api_base", "model", "request_style")):
            self._style_cache.clear()
            self._negative_ok.clear()
        saved = await self._persist_config()
        return self._json({"code": 0, "data": {"saved": bool(saved), "applied": sorted(data)}})

    async def _persist_config(self):
        """把配置写回 AstrBot；配置对象不支持保存时只留内存，不报错。"""
        cfg = self.config
        saver = getattr(cfg, "save_config_async", None)
        if callable(saver):
            try:
                return bool(await saver())
            except Exception as exc:
                logger.warning("[amo_image] 配置落盘失败: %s" % exc)
        saver = getattr(cfg, "save_config", None)
        if callable(saver):
            try:
                await asyncio.to_thread(saver)
                return True
            except Exception as exc:
                logger.warning("[amo_image] 配置落盘失败: %s" % exc)
        return False

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
