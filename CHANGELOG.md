# Changelog

## 0.4.0

首个版本：把「生图」从陪伴插件里拆出来，单独做成可插拔的第三块。

- 触发：私聊/群聊说「画一张…」「帮我画…」「生成图片…」，或 `/amoimage draw <提示词>`；
- 群聊引用与 @ 规则：群里可配 `require_at_in_group`，发图默认不带引用（和陪伴插件的私聊策略一致）；
- 联动数据插件：从 `Astr_Amo_data` 读最近几句对话补进提示词，插件缺失或报错静默降级；
- 人设外观：`character_appearance` 拼在提示词最前面；
- 后端：OpenAI 兼容 `POST {api_base}/images/generations`，同时支持 `url` 与 `b64_json` 返回；
- 额度：按人冷却 + 每日上限，`/amoimage status` 可查；
- 失败一律降级成 `error_reply` 一句话，不抛异常、不影响聊天主链路；
- 测试：24 passed（stub 掉 `astrbot.*`，不连网）。
