# -*- coding: utf-8 -*-
"""生图后端抽象层。

把「怎么发请求、怎么认响应」从插件主逻辑里拆出来，一个后端一个类：

- ``openai``：``POST {api_base}/images/generations``，OpenAI 兼容接口（OpenAI / 硅基流动 /
  智谱 / 百炼 / one-api / new-api 都走这条）；
- ``openai_chat``：``POST {api_base}/chat/completions``，图片从回复文本（markdown 链接）
  或 ``message.images`` 里取，适合只开 Chat 通道的代理；
- ``gemini``：Google 原生 ``models/{model}:generateContent``，图片在 ``inlineData`` 里；
- ``custom``：自己填完整 URL、请求体模板和响应路径，接 ComfyUI 反代、火山方舟之类。

参考过 astrbot_plugin_omnidraw 的架构（Provider 拆分、响应宽容解析、WebUI 配置页），
但代码是本仓库自己写的，没有复制其实现（该项目 Python 部分没有 LICENSE）。
"""

from __future__ import annotations

import base64
import json
import re
from urllib.parse import urlparse

DEFAULT_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-image"
DEFAULT_CUSTOM_BODY = (
    '{"prompt": "{prompt}", "model": "{model}", "size": "{size}", "n": {n}}'
)
ASPECT_RATIOS = ("1:1", "3:4", "4:3", "9:16", "16:9", "2:3", "3:2", "4:5", "5:4")


class APIError(RuntimeError):
    """后端返回 >=400 时抛这个，带上状态码方便决定要不要换一套参数重试。"""

    def __init__(self, status, message):
        super().__init__("生图接口 %s: %s" % (status, message))
        self.status = int(status or 0)


class ImageOptions:
    """一次生图的通用参数；各后端按自己认识的字段取用。"""

    __slots__ = ("size", "n", "quality", "style", "negative", "aspect_ratio")

    def __init__(self, size="1024x1024", n=1, quality="", style="",
                 negative="", aspect_ratio=""):
        self.size = str(size or "")
        self.n = max(1, int(n or 1))
        self.quality = str(quality or "")
        self.style = str(style or "")
        self.negative = str(negative or "")
        self.aspect_ratio = str(aspect_ratio or "")


# ---------- 响应解析：不认厂商，只认形状 ----------

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
    if len(text) < 40:
        return None
    try:
        return base64.b64decode(text, validate=True)
    except Exception:
        return None


def _image_from_text(text):
    """从普通文本里捞图：markdown 链接、裸 URL、data:image。"""
    value = str(text or "").strip()
    if not value:
        return None
    if value.startswith("data:image"):
        data = _as_b64(value)
        return ("b64", data) if data else None
    match = re.search(r"!\[[^\]]*\]\(\s*(https?://[^)\s]+|data:image[^)\s]+)\s*\)", value)
    if match:
        target = match.group(1)
        if target.startswith("data:image"):
            data = _as_b64(target)
            return ("b64", data) if data else None
        return ("url", target)
    match = re.search(r"(https?://[^\s)\]\"'<>]+)", value)
    if match:
        url = match.group(1).rstrip(".,;:!?，。；：！？")
        if re.search(r"\.(?:png|jpe?g|webp|gif)(?:[?#].*)?$", url.lower()):
            return ("url", url)
    return None


def _walk_candidates(node, depth=0):
    """把各家返回结构摊平成「可能是 url / base64 的叶子」。"""
    if depth > 8:
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
    is_inline = bool(
        isinstance(node.get("data"), str)
        and ("mimeType" in node or "mime_type" in node)
    )
    if any(key in node for key in ("url", "image_url", "imageUrl", "b64_json", "b64", "base64")) or is_inline:
        yield node
    for key in ("data", "images", "artifacts", "results", "output", "outputs",
                "result", "image", "urls", "items", "choices", "message",
                "content", "parts", "candidates", "inlineData", "inline_data"):
        if key in node:
            yield from _walk_candidates(node[key], depth + 1)


def parse_image_response(payload) -> tuple:
    """解析生图响应，返回 (kind, value)：kind 是 url 或 b64。"""
    if not isinstance(payload, (str, list, dict)):
        raise ValueError("响应不是 JSON")

    for item in _walk_candidates(payload):
        if isinstance(item, str):
            url = _as_url(item)
            if url:
                return ("url", url)
            found = _image_from_text(item)
            if found:
                return found
            data = _as_b64(item)
            if data:
                return ("b64", data)
            continue
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("data"), str) and ("mimeType" in item or "mime_type" in item):
            data = _as_b64(item.get("data"))
            if data:
                return ("b64", data)
        for key in ("url", "image_url", "imageUrl"):
            url = _as_url(item.get(key))
            if url:
                return ("url", url)
        for key in ("b64_json", "b64", "base64", "image_base64"):
            data = _as_b64(item.get(key))
            if data:
                return ("b64", data)
    raise ValueError("响应里找不到图片（data / images / choices / candidates 均无图）")


def ratio_from_size(size):
    """把 1024x1024 这类尺寸换算成最接近的宽高比。"""
    match = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", str(size or ""))
    if not match:
        return ""
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        return ""
    ratio = width / height
    best = min(
        ASPECT_RATIOS,
        key=lambda item: abs(ratio - int(item.split(":")[0]) / int(item.split(":")[1])),
    )
    return best


def dig(payload, path):
    """按 ``data.images[0].url`` 这种路径取值；取不到返回 None。"""
    current = payload
    for part in re.split(r"\.(?![^\[]*\])", str(path or "").strip()):
        if not part:
            continue
        match = re.fullmatch(r"([^\[\]]*)((?:\[\d+\])*)", part)
        if not match:
            return None
        name, indexes = match.group(1), match.group(2)
        if name:
            if not isinstance(current, dict) or name not in current:
                return None
            current = current[name]
        for index in re.findall(r"\[(\d+)\]", indexes):
            if not isinstance(current, list):
                return None
            position = int(index)
            if position >= len(current):
                return None
            current = current[position]
    return current


# ---------- Provider ----------

class BaseProvider:
    name = "base"

    def __init__(self, plugin):
        self.plugin = plugin

    def cfg(self, key, default=None):
        return self.plugin._cfg(key, default)

    def base_url(self, default=""):
        return str(self.cfg("api_base", "") or default).strip().rstrip("/")

    def api_key(self):
        return str(self.cfg("api_key", "") or "").strip()

    def bearer_headers(self):
        headers = {"Content-Type": "application/json"}
        key = self.api_key()
        if key:
            headers["Authorization"] = "Bearer " + key
        return headers

    def request(self, prompt, options):
        raise NotImplementedError

    def parse(self, payload):
        return parse_image_response(payload)

    async def generate(self, prompt, options):
        url, headers, body = self.request(prompt, options)
        payload = await self.plugin._post_json(url, body, headers)
        kind, value = self.parse(payload)
        return await self.plugin._download(value) if kind == "url" else value


class OpenAIImagesProvider(BaseProvider):
    """OpenAI 兼容的 /images/generations；保留参数风格与负面词回退。"""

    name = "openai"

    async def generate(self, prompt, options):
        plugin = self.plugin
        endpoint = plugin._endpoint()
        errors = []
        negative = plugin._negative() if plugin._negative_ok.get(endpoint, True) else ""
        for style in plugin._style_order():
            variants = [plugin._body(prompt, style, negative)]
            if negative:
                variants.append(plugin._body(prompt, style, ""))
            for index, body in enumerate(variants):
                try:
                    payload = await plugin._post_json(endpoint, body, plugin._headers())
                except APIError as exc:
                    errors.append(str(exc))
                    if index == 0 and negative:
                        plugin._negative_ok[endpoint] = False
                    if exc.status in plugin.RETRY_STATUS:
                        continue
                    raise
                plugin._style_cache[endpoint] = style
                if negative:
                    plugin._negative_ok[endpoint] = index == 0
                kind, value = parse_image_response(payload)
                return await plugin._download(value) if kind == "url" else value
        raise RuntimeError("；".join(errors[-2:]) or "生图接口没有可用的参数风格")


class OpenAIChatProvider(BaseProvider):
    """Chat 通道：/chat/completions，图片从回复文本或 message.images 里取。"""

    name = "openai_chat"

    def request(self, prompt, options):
        base = self.base_url("https://api.openai.com/v1")
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        model = str(self.cfg("model", "") or "").strip()
        if not model:
            raise ValueError("Chat 通道要配 model")
        body = {"model": model, "messages": [{"role": "user", "content": str(prompt)}]}
        if self.plugin._bool("chat_modalities", False):
            body["modalities"] = ["image", "text"]
        return url, self.bearer_headers(), body


class GeminiProvider(BaseProvider):
    """Google 原生 generateContent；图片在 inlineData 里。"""

    name = "gemini"

    def request(self, prompt, options):
        key = self.api_key()
        if not key:
            raise ValueError("Gemini 要配 api_key")
        base = self.base_url(DEFAULT_GEMINI_BASE)
        parsed = urlparse(base)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Gemini 的 api_base 要是完整的 http(s) 地址")
        model = str(self.cfg("model", "") or DEFAULT_GEMINI_MODEL).strip()
        if model.startswith("models/"):
            model = model.split("/", 1)[1]
        if base.endswith(":generateContent"):
            url = base
        elif base.endswith("/models"):
            url = base + "/" + model + ":generateContent"
        else:
            url = base + "/models/" + model + ":generateContent"
        generation = {"responseModalities": ["TEXT", "IMAGE"]}
        ratio = options.aspect_ratio or ratio_from_size(options.size)
        if ratio:
            generation["imageConfig"] = {"aspectRatio": ratio}
        body = {
            "contents": [{"role": "user", "parts": [{"text": str(prompt)}]}],
            "generationConfig": generation,
        }
        headers = {"Content-Type": "application/json", "x-goog-api-key": key}
        return url, headers, body


class CustomProvider(BaseProvider):
    """自定义 HTTP 端点：完整 URL + 请求体模板 + 响应路径。"""

    name = "custom"

    def request(self, prompt, options):
        url = str(self.cfg("custom_url", "") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("自定义后端要填完整的 custom_url")
        template = str(self.cfg("custom_body", "") or DEFAULT_CUSTOM_BODY)
        text = template
        text = text.replace("{prompt}", json.dumps(str(prompt), ensure_ascii=False)[1:-1])
        text = text.replace("{model}", json.dumps(str(self.cfg("model", "") or ""))[1:-1])
        text = text.replace("{size}", json.dumps(str(options.size or "1024x1024"))[1:-1])
        text = text.replace("{n}", str(int(options.n or 1)))
        try:
            body = json.loads(text)
        except Exception as exc:
            raise ValueError("custom_body 不是合法 JSON：%s" % exc)
        if not isinstance(body, dict):
            raise ValueError("custom_body 要是一个 JSON 对象")
        return url, self.bearer_headers(), body

    def parse(self, payload):
        path = str(self.cfg("custom_response_path", "") or "").strip()
        if path:
            value = dig(payload, path)
            if isinstance(value, str):
                url = _as_url(value)
                if url:
                    return ("url", url)
                found = _image_from_text(value)
                if found:
                    return found
                data = _as_b64(value)
                if data:
                    return ("b64", data)
            raise ValueError("custom_response_path 没取到图片：%s" % path)
        return parse_image_response(payload)


def build_provider(plugin):
    """按配置挑后端；认不出的值退回 OpenAI 兼容。"""
    kind = str(plugin._cfg("provider", "openai") or "openai").strip().lower()
    if kind in ("chat", "openai_chat", "openai-chat"):
        return OpenAIChatProvider(plugin)
    if kind in ("gemini", "google"):
        return GeminiProvider(plugin)
    if kind in ("custom", "custom_endpoint", "自定义"):
        return CustomProvider(plugin)
    return OpenAIImagesProvider(plugin)
