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

PLUGIN_NAME = "Astrbot_Amo_Image"
PLUGIN_VERSION = "0.4.6"

DATA_PLUGIN_NAMES = (
    "Astr_Amo_data",
    "astrbot_plugin_amo_data",
    "amo_data",
)

DEFAULT_KEYWORDS = ["画一张", "画个", "帮我画", "生成图片", "来张图", "画张"]

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


def match_keyword(text: str, keywords) -> str:
    """返回命中的触发词（没命中返回空串）。长的优先，避免「画个」抢了「画个图」。"""
    body = str(text or "")
    for word in sorted([str(k) for k in (keywords or []) if str(k)], key=len, reverse=True):
        if word and word in body:
            return word
    return ""


def parse_image_response(payload) -> tuple:
    """解析生图响应，返回 (kind, value)：kind 是 url 或 b64。

    兼容 data[0].url / data[0].b64_json；也容忍后端把 data 直接给成字符串列表。
    """
    if not isinstance(payload, dict):
        raise ValueError("响应不是 JSON 对象")
    data = payload.get("data")
    if not data:
        raise ValueError("响应里没有 data")
    first = data[0] if isinstance(data, list) else data
    if isinstance(first, str):
        if first.startswith("http://") or first.startswith("https://"):
            return ("url", first)
        return ("b64", base64.b64decode(first))
    if not isinstance(first, dict):
        raise ValueError("data[0] 不是对象")
    url = str(first.get("url") or "").strip()
    if url.startswith("http://") or url.startswith("https://"):
        return ("url", url)
    b64 = str(first.get("b64_json") or first.get("b64") or "").strip()
    if b64:
        if b64.startswith("data:"):
            b64 = b64.split(",", 1)[-1]
        return ("b64", base64.b64decode(b64))
    raise ValueError("data[0] 既没有 url 也没有 b64_json")


def build_prompt(appearance: str, prompt: str, context_lines=None) -> str:
    """拼最终提示词：外观 → 用户要求 → 剧情上下文。"""
    parts = []
    if str(appearance or "").strip():
        parts.append(str(appearance).strip())
    if str(prompt or "").strip():
        parts.append(str(prompt).strip())
    if context_lines:
        tail = "；".join([str(x).strip() for x in context_lines if str(x).strip()][-6:])
        if tail:
            parts.append("画面要接得上这段对话：%s" % tail)
    return "，".join(parts)


class AmoImagePlugin(Star):
    """生图插件：认人设、接上下文、只画图。"""

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self._last_call = {}   # uid -> 上次生图时间
        self._daily = {}       # uid -> [日期字符串, 次数]
        self._history = deque(maxlen=40)   # 最近几次生图（面板用）
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

    # ---------- 数据插件联动 ----------

    @staticmethod
    def _star_instance(meta):
        """AstrBot 各版本把实例放在不同属性上，挨个试。"""
        if meta is None:
            return None
        for attr in ("star_cls", "instance", "star", "plugin"):
            value = getattr(meta, attr, None)
            if value is not None and hasattr(value, "mem"):
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
            if callable(getattr(mem, "messages_since", None)):
                rows = await mem.messages_since(umo, uid=uid, since=0, limit=limit)
            elif callable(getattr(mem, "recall_messages", None)):
                got = await mem.recall_messages(umo, query="", umo=umo, uid=uid, limit=limit)
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

    def _is_at_me(self, event) -> bool:
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

    def _quota_ok(self, uid: str) -> tuple:
        """冷却 + 每日次数。返回 (ok, 提示)。"""
        now = time.time()
        cooldown = max(0, self._int("cooldown_seconds", 30))
        last = self._last_call.get(uid, 0)
        if cooldown and last and now - last < cooldown:
            return (False, "刚画过，等我缓一下")
        daily = max(0, self._int("daily_limit", 20))
        if daily:
            today = time.strftime("%Y-%m-%d")
            record = self._daily.get(uid) or [today, 0]
            if record[0] != today:
                record = [today, 0]
            if record[1] >= daily:
                self._daily[uid] = record
                return (False, "今天画得够多啦，明天再画")
            self._daily[uid] = record
        return (True, "")

    def _mark_call(self, uid: str):
        self._last_call[uid] = time.time()
        today = time.strftime("%Y-%m-%d")
        record = self._daily.get(uid) or [today, 0]
        if record[0] != today:
            record = [today, 0]
        record[1] = int(record[1]) + 1
        self._daily[uid] = record

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

    def _body(self, prompt: str) -> dict:
        body = {
            "model": str(self._cfg("model", "gpt-image-1")),
            "prompt": prompt,
            "n": max(1, self._int("n", 1)),
            "size": str(self._cfg("size", "1024x1024")),
        }
        quality = str(self._cfg("quality", "") or "").strip()
        if quality:
            body["quality"] = quality
        style = str(self._cfg("style", "") or "").strip()
        if style:
            body["style"] = style
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
                        raise RuntimeError("生图接口 %s: %s" % (resp.status, text[:200]))
                    return json.loads(text)
        return await asyncio.to_thread(self._post_json_blocking, url, raw, timeout)

    def _post_json_blocking(self, url: str, raw: bytes, timeout: float) -> dict:
        import urllib.request

        req = urllib.request.Request(url, data=raw, headers=self._headers(), method="POST")
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

    async def _generate(self, prompt: str) -> bytes:
        payload = await self._post_json(self._endpoint(), self._body(prompt))
        kind, value = parse_image_response(payload)
        if kind == "url":
            return await self._download(value)
        return value

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
                "daily_limit": self._int("daily_limit", 20),
                "users_today": self._today_used(),
                "calls_total": self._calls_total,
                "history_size": len(self._history),
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
            "keywords", "cooldown_seconds", "daily_limit", "reply_prefix", "error_reply",
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
        final_prompt = build_prompt(self._cfg("character_appearance", ""), prompt, [])
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

    @filter.event_message_type(filter.EventMessageType.ALL)
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
            if group and self._bool("require_at_in_group", False) and not self._is_at_me(event):
                return
            keyword = match_keyword(text, self._keywords())
            if not keyword:
                return
            prompt = clean_prompt(text, keyword)
            if len(prompt) < 2:
                yield event.plain_result("想画什么？说具体点")
                return
            async for result in self._draw(event, prompt):
                yield result
        except Exception as exc:  # 兜底：绝不把异常抛回框架
            logger.warning("[amo_image] 处理消息失败: %s" % exc)

    async def _draw(self, event, prompt: str):
        uid = str(getattr(event, "get_sender_id", lambda: "")() or "")
        ok, note = self._quota_ok(uid)
        if not ok:
            yield event.plain_result(note or str(self._cfg("error_reply", "画不出来，等会儿再试试")))
            return
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        ctx = await self._context_lines(umo, uid)
        final_prompt = build_prompt(self._cfg("character_appearance", ""), prompt, ctx)
        self._mark_call(uid)
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
        """生图插件的状态与手动出图：/amoimage status | /amoimage draw <提示词>"""
        args = [str(x) for x in (args or []) if str(x).strip()]
        action = args[0].lower() if args else "status"
        if action in ("status", "状态"):
            uid = str(getattr(event, "get_sender_id", lambda: "")() or "")
            today = time.strftime("%Y-%m-%d")
            record = self._daily.get(uid) or [today, 0]
            used = record[1] if record[0] == today else 0
            limit = self._int("daily_limit", 20)
            yield event.plain_result(
                "生图：%s\n后端：%s\n模型：%s\n今日：%s/%s" % (
                    "开" if self._bool("enable", True) else "关",
                    str(self._cfg("api_base", "") or "未配置"),
                    str(self._cfg("model", "") or "-"),
                    used,
                    limit if limit else "不限",
                )
            )
            return
        if action in ("draw", "画"):
            prompt = " ".join(args[1:]).strip()
            if not prompt:
                yield event.plain_result("用法：/amoimage draw <提示词>")
                return
            async for result in self._draw(event, prompt):
                yield result
            return
        yield event.plain_result("用法：/amoimage status 或 /amoimage draw <提示词>")
