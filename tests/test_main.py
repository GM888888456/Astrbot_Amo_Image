# -*- coding: utf-8 -*-
"""Astrbot_Amo_Image 单测：不连网、不依赖 AstrBot 运行时。"""

from __future__ import annotations

import base64

import pytest

import astrbot_stub
from astrbot_stub import _Event, _Image, _Plain, collect

main = astrbot_stub.plugin_module


def make_plugin(**cfg):
    conf = {
        "api_base": "https://example.invalid/v1",
        "api_key": "k-test",
        "model": "test-model",
        "cooldown_seconds": 0,
        "daily_limit": 0,
    }
    conf.update(cfg)
    return main.AmoImagePlugin(context=astrbot_stub._Context(), config=conf)


class FakeMem:
    def __init__(self, rows=None, fail=False):
        self.rows = rows or []
        self.fail = fail

    async def messages_since(self, bot_id, uid="", since_ts=0, limit=10):
        if self.fail:
            raise RuntimeError("db down")
        return self.rows[:limit]

    async def recall_messages(self, bot_id, query="", **kw):
        if self.fail:
            raise RuntimeError("db down")
        return {"messages": self.rows, "label": ""}


class FakeStar:
    def __init__(self, rows=None, fail=False):
        self.mem = FakeMem(rows, fail)


def test_clean_prompt_strips_keyword_and_punctuation():
    assert main.clean_prompt("画一张，一只在雨里的猫。", "画一张") == "一只在雨里的猫"
    assert main.clean_prompt("帮我画：海边的灯塔", "帮我画") == "海边的灯塔"
    assert main.clean_prompt("今天天气不错", "画一张") == "今天天气不错"


def test_match_keyword_prefers_longest():
    kws = ["画个", "画个图"]
    assert main.match_keyword("帮我画个图试试", kws) == "画个图"
    assert main.match_keyword("没有触发词", kws) == ""


def test_parse_url_response():
    kind, value = main.parse_image_response({"data": [{"url": "https://x.test/a.png"}]})
    assert kind == "url" and value == "https://x.test/a.png"


def test_parse_b64_response():
    raw = b"PNGDATA" * 8
    payload = {"data": [{"b64_json": base64.b64encode(raw).decode()}]}
    kind, value = main.parse_image_response(payload)
    assert kind == "b64" and value == raw


def test_parse_b64_data_uri():
    raw = b"abc" * 20
    payload = {"data": [{"b64_json": "data:image/png;base64," + base64.b64encode(raw).decode()}]}
    assert main.parse_image_response(payload) == ("b64", raw)


@pytest.mark.parametrize("payload", [{}, {"data": []}, {"data": [{}]}, "nope"])
def test_parse_bad_response_raises(payload):
    with pytest.raises(ValueError):
        main.parse_image_response(payload)


def test_parse_siliconflow_images_shape():
    kind, value = main.parse_image_response({"images": [{"url": "https://x.test/b.png"}], "seed": 1})
    assert kind == "url" and value == "https://x.test/b.png"


def test_parse_output_urls_shape():
    kind, value = main.parse_image_response({"output": {"urls": ["https://x.test/c.png"]}})
    assert kind == "url" and value == "https://x.test/c.png"


def test_parse_artifacts_base64_shape():
    raw = b"ART" * 30
    payload = {"artifacts": [{"base64": base64.b64encode(raw).decode()}]}
    kind, value = main.parse_image_response(payload)
    assert kind == "b64" and value == raw


def test_body_styles():
    p = make_plugin(size="1024x1024")
    openai = p._body("猫", "openai")
    aliases = p._body("猫", "aliases")
    assert openai["size"] == "1024x1024" and openai["n"] == 1
    assert aliases["image_size"] == "1024x1024" and aliases["batch_size"] == 1
    assert "size" not in aliases and "image_size" not in openai


def test_generate_retries_with_alias_style_on_400():
    p = make_plugin()
    calls = []

    async def fake_post(url, body):
        calls.append(dict(body))
        if "size" in body:
            raise main.APIError(400, "unknown field size")
        return {"images": [{"url": "https://x.test/d.png"}]}

    async def fake_download(url):
        return b"IMG"

    p._post_json = fake_post
    p._download = fake_download
    out = collect(p._generate("猫"))
    assert out == b"IMG"
    assert "image_size" in calls[-1]


def test_photo_keyword_triggers_draw():
    p = make_plugin()
    seen = {}

    async def fake_draw(event, prompt):
        seen["prompt"] = prompt
        if False:
            yield None

    p._draw = fake_draw
    ev = _Event("给我拍张照", private=True)
    collect(p.on_message(ev))
    assert seen["prompt"] == "自拍，日常光线，自然表情，看向镜头"


def test_build_prompt_order_and_context():
    got = main.build_prompt("银灰长发", "在雨里撑伞", ["他说想一起去看海", "她说好啊"])
    assert got.startswith("银灰长发，在雨里撑伞")
    assert "画面要接得上这段对话" in got
    assert main.build_prompt("", "猫") == "猫"


def test_gate_private_and_group_switch():
    p = make_plugin(allow_private=False)
    assert collect(p.on_message(_Event("画一张猫", private=True))) == []
    p2 = make_plugin(allow_group=False)
    assert collect(p2.on_message(_Event("画一张猫", private=False))) == []


def test_gate_require_at_in_group():
    p = make_plugin(require_at_in_group=True)
    assert collect(p.on_message(_Event("画一张猫", private=False, at=False))) == []
    # 命中 @ 才继续（这里不真生图，只验证没有在闸门处被拦掉）
    p._generate = _raise  # noqa: F821  - 由下面定义
    out = collect(p.on_message(_Event("画一张猫", private=False, at=True)))
    assert out and out[0].kind == "plain"


def _raise(prompt):
    raise RuntimeError("boom")


def test_message_without_keyword_is_ignored():
    p = make_plugin()
    assert collect(p.on_message(_Event("今天吃什么", private=True))) == []


def test_cooldown_blocks_second_call():
    p = make_plugin(cooldown_seconds=999)
    p._mark_call("u1")
    ok, note = p._quota_ok("u1")
    assert not ok and note


def test_daily_limit_blocks_after_limit():
    p = make_plugin(daily_limit=1)
    p._mark_call("u2")
    ok, _ = p._quota_ok("u2")
    assert not ok


def test_context_lines_from_data_plugin():
    p = make_plugin()
    p._data_plugin = lambda: FakeStar(rows=[{"content": "他说想去看海"}])
    lines = collect(p._context_lines("umo", "u1"))
    assert lines == ["他说想去看海"]


def test_context_lines_degrades_when_data_plugin_fails():
    p = make_plugin()
    p._data_plugin = lambda: FakeStar(fail=True)
    assert collect(p._context_lines("umo", "u1")) == []


def test_context_lines_disabled():
    p = make_plugin(use_memory_context=False)
    p._data_plugin = lambda: FakeStar(rows=[{"content": "x"}])
    assert collect(p._context_lines("umo", "u1")) == []


def test_draw_success_returns_image_chain():
    p = make_plugin(reply_prefix="画好啦")
    p._generate = lambda prompt: _async(b"\x89PNG\r\n")
    out = collect(p._draw(_Event("画一张猫", private=False), "猫"))
    assert len(out) == 1 and out[0].kind == "chain"
    chain = out[0].payload
    plains = [c for c in chain if isinstance(c, _Plain)]
    images = [c for c in chain if isinstance(c, _Image)]
    assert plains and plains[0].text == "画好啦"
    assert images and images[0].file.startswith("bytes:")
    # 群聊默认引用原消息
    assert type(chain[0]).__name__ == "_Reply"


def test_draw_error_returns_persona_reply():
    p = make_plugin(error_reply="先不画了")
    p._generate = lambda prompt: _async_raise(RuntimeError("502"))
    out = collect(p._draw(_Event("画一张猫"), "猫"))
    assert len(out) == 1 and out[0].kind == "plain" and out[0].payload == "先不画了"


def test_body_omits_empty_quality_and_style():
    p = make_plugin(size="1024x1024")
    body = p._body("猫")
    assert body["model"] == "test-model"
    assert body["prompt"] == "猫"
    assert "quality" not in body and "style" not in body


def test_endpoint_builds_without_double_slash():
    p = make_plugin(api_base="https://x.test/v1/")
    assert p._endpoint() == "https://x.test/v1/images/generations"


def test_short_prompt_asks_for_detail():
    p = make_plugin()
    out = collect(p.on_message(_Event("画一张", private=True)))
    assert out and out[0].kind == "plain" and "想画什么" in out[0].payload


def test_web_routes_registered():
    p = make_plugin()
    routes = [item["route"] for item in p.context.web_apis]
    assert "/Astrbot_Amo_Image/status" in routes
    assert "/Astrbot_Amo_Image/history" in routes
    assert "/Astrbot_Amo_Image/test" in routes
    assert "/Astrbot_Amo_Image/config" in routes


def test_api_status_shape_and_masked_key():
    p = make_plugin(api_key="sk-abcdefghijklmnop")
    data = collect(p.api_status())["data"]
    assert data["name"] == "Astrbot_Amo_Image"
    assert data["enabled"] is True
    assert data["api_key_masked"] == "sk-a…mnop"
    assert "api_key" not in data


def test_api_history_records_draw_result():
    p = make_plugin()
    p._generate = lambda prompt: _async(b"12345")
    collect(p._draw(_Event("画一张猫"), "猫"))
    items = collect(p.api_history())["data"]["items"]
    assert len(items) == 1
    assert items[0]["ok"] is True and items[0]["bytes"] == 5


def test_api_test_returns_data_uri():
    p = make_plugin()
    p._generate = lambda prompt: _async(b"PNG")
    out = collect(p.api_test())
    assert out["code"] == 0
    assert out["data"]["image"].startswith("data:image/png;base64,")


def test_api_test_reports_failure():
    p = make_plugin()
    p._generate = lambda prompt: _async_raise(RuntimeError("boom"))
    out = collect(p.api_test())
    assert out["code"] == 1 and "boom" in out["message"]


def test_handled_message_stops_event():
    """生图处理过就必须 stop_event，否则陪伴/LLM 会再补一条。"""
    p = make_plugin()
    p._generate = lambda prompt: _async(b"PNG")
    ev = _Event("画一张猫", private=False)
    collect(p.on_message(ev))
    assert ev.stopped is True

    ev2 = _Event("画一张", private=True)
    collect(p.on_message(ev2))
    assert ev2.stopped is True


def test_bot_id_from_companion():
    p = make_plugin()

    class FakeCompanion:
        def _data_bot_id(self):
            return "botX"

    p._companion = lambda: FakeCompanion()
    assert p._bot_id() == "botX"


def test_command_status_reports_backend():
    p = make_plugin()
    out = collect(p.amoimage(_Event(), "status"))
    assert out and "https://example.invalid/v1" in out[0].payload


async def _async(value):
    return value


async def _async_raise(exc):
    raise exc
