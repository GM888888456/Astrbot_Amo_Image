# Changelog

## 0.4.4

### 可视化面板

- `views/dashboard/`：AstrBot WebUI 里点插件卡片就能打开「面板」；
- 后端状态：开关、地址、模型、尺寸、密钥掩码、今日用量、累计次数、运行时长；
- 试画：面板里填提示词直接出一张，返回 `data:image/png;base64` 即时预览，不占聊天额度；
- 最近 40 次记录：时间、提示词、成败、耗时、字节；
- 深浅色跟随按钮切换，页面每 15 秒刷新一次。

### 后端接口

- `GET /Astrbot_Amo_Image/status`、`GET /history`、`GET /config`、`POST /test`；
- `api_key` 只回掩码，面板拿不到完整密钥；
- 6.8.0 的 `views/` 扫描机制：`views/<name>/index.html` 自动被 AstrBot 加载，脚本走 `window.AstrbotPluginView` bridge。

测试 29 passed（含 5 条面板接口用例）。

## 0.4.2

- 显示名改为 **我想和你度日常**（插件 ID `Astrbot_Amo_Image` 不变，配置与数据不受影响）；
- 图标换成陪伴插件同款 `logo.png`；
- 功能与 0.4.0 一致，测试 24 passed。

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
