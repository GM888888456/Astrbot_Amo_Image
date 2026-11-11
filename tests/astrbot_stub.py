# -*- coding: utf-8 -*-
"""把 astrbot.* 这套依赖 mock 掉，让插件能在没有 AstrBot 的机器上跑单测。"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import logging
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Chain(list):
    pass


class _Result:
    def __init__(self, kind, payload):
        self.kind = kind
        self.payload = payload

    def __repr__(self):
        return "<Result %s %r>" % (self.kind, self.payload)


class _Plain:
    def __init__(self, text=""):
        self.text = text

    def __repr__(self):
        return "<Plain %r>" % self.text


class _Reply:
    def __init__(self, id=None):
        self.id = id


class _Image:
    def __init__(self, file=None):
        self.file = file

    @staticmethod
    def fromBytes(data):
        return _Image("bytes:" + base64.b64encode(data).decode())

    @staticmethod
    def fromBase64(data):
        return _Image("base64://" + data)


class _Context:
    """最小 Context：记录注册的面板路由，供测试断言。"""

    def __init__(self, stars=None):
        self._stars = stars or {}
        self.web_apis = []

    def register_web_api(self, route, handler, methods=None, desc=""):
        # 兼容两种签名：companion 风格 (route, handler, methods, desc)
        if callable(handler):
            self.web_apis.append({"route": route, "methods": list(methods or []), "desc": desc})
        else:
            self.web_apis.append({"route": route, "methods": list(handler or []), "desc": desc})

    def get_registered_star(self, name):
        return _Meta(self._stars[name]) if name in self._stars else None

    def get_all_stars(self):
        return [_Meta(v) for v in self._stars.values()]


class _Meta:
    def __init__(self, star):
        self.star_cls = star


class _Event:
    def __init__(self, text="", private=True, at=False, uid="10001", umo="umo", message=None):
        self.message_str = text
        self._private = private
        self._at = at
        self._uid = uid
        self.unified_msg_origin = umo
        self.message_obj = types.SimpleNamespace(message_id="mid-1", message=message or [])
        self.is_at_or_wake_command = at
        self.results = []
        self.stopped = False

    def is_private_chat(self):
        return self._private

    def get_sender_id(self):
        return self._uid

    def get_self_id(self):
        return "99999"

    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        r = _Result("plain", text)
        self.results.append(r)
        return r

    def image_result(self, path):
        r = _Result("image_path", path)
        self.results.append(r)
        return r

    def chain_result(self, chain):
        r = _Result("chain", chain)
        self.results.append(r)
        return r


class _Filter:
    class EventMessageType:
        ALL = "*"

    @staticmethod
    def event_message_type(_type, **kwargs):
        def deco(fn):
            return fn

        return deco

    @staticmethod
    def command(_name):
        def deco(fn):
            return fn

        return deco


def _install_stub():
    if "astrbot" in sys.modules:
        return

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = logging.getLogger("astrbot-test")
    api.logger.addHandler(logging.NullHandler())

    event_mod = types.ModuleType("astrbot.api.event")
    event_mod.filter = _Filter
    event_mod.AstrMessageEvent = _Event

    star_mod = types.ModuleType("astrbot.api.star")

    class _Star:
        def __init__(self, context=None):
            self.context = context

    star_mod.Context = _Context
    star_mod.Star = _Star

    comp_mod = types.ModuleType("astrbot.api.message_components")
    comp_mod.Image = _Image
    comp_mod.Plain = _Plain
    comp_mod.Reply = _Reply

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.star"] = star_mod
    sys.modules["astrbot.api.message_components"] = comp_mod
    astrbot.api = api


def _load_plugin():
    _install_stub()
    spec = importlib.util.spec_from_file_location("amo_image_main", os.path.join(ROOT, "main.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collect(agen):
    """同步跑一个 async generator 或协程，收集结果。"""

    async def _run():
        if hasattr(agen, "__aiter__"):
            return [x async for x in agen]
        return await agen

    return asyncio.run(_run())


plugin_module = _load_plugin()
