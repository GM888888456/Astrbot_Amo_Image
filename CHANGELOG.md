# Changelog

## 0.10.0（换装冷却、保底服装、负面提示词、手动换装）

上一版把「按天固定」做了，但还有三个洞。

### 换装会连着跳

日程里「起床换衣服」「出门换装」两条挨着，旧逻辑两条都算，造型直接跳两套；
同一条日程写重复了还会重复计数。

- 新增 `outfit_change_cooldown_minutes`（默认 60）：两次换装至少隔这么久才算两次；
- 同一分钟重复的换装事件只算一次；
- 只算已经开始的事件，没到点的不算。

### 服装池清空就没有服装

`character_outfits` 留空时旧逻辑直接不写服装，模型就随机穿。

- 内置一组保底服装（`DEFAULT_OUTFITS`），池空时自动顶上；
- 提示词里永远有「今天的样子」。

### 负面提示词

- 新增 `negative_prompt`，默认是一串英文负面词（换脸、换发型、换衣服、换地点、糊、多指）；
- Kolors / SiliconFlow 这类认 `negative_prompt` 的后端会带上；OpenAI images 不认，
  会自动去掉重试并记住，不会因此报错。

### 手动换装

- 发「换一套衣服」（或 `/amoimage outfit`）立刻换下一套，不受日程和冷却限制；
- 记录按天存在 `look_state.json`，重启后当天仍然有效，换天自动归零；
- 只有整句就是指令才算，聊天里说「我想换一套衣服再出门」不会误触发。

### 其他

- 陪伴的分享模板本身带 `{place}`，提示词组装不再重复追加「此刻在××」；
- 测试 52 passed（新增：冷却、重复事件、未来事件、保底池、手动换装、跨路径同一套造型、负面提示词回退）。

## 0.9.0（人物 / 服装 / 场景一致性）

### 同一个人，不再一秒 JK 一秒汉服

- 新增 `character_hairstyle`：固定发型，每次都写进提示词；
- 新增 `character_outfits`：服装池，**按天固定**——`crc32(日期) % 套数` 决定今天穿哪套，
  同一天怎么画都是同一套，重启也不变；
- 日程里出现「换衣服 / 换装 / 洗澡 / 换上 / 换一身」这类活动，且已经开始，才算一次换装，
  当天造型顺延一套；
- 提示词末尾补一句「同一个人，脸、发型、服装保持一致，不要换成别的装扮」。

### 场景跟着日程走，不瞬移

- 新增 `lock_scene_to_schedule`（默认开）：从陪伴的 `state.current_activity()` 取此刻地点，
  写进提示词「此刻在××」；
- 聊天生图、`/amoimage draw`、面板试画、陪伴主动分享，四条路径都走同一个 `_final_prompt()`，
  不会出现「聊天里在景区、分享时在别处」。

测试 43 passed（新增：同日造型稳定、换装后变、场景取日程、关闭锁定、提示词约束）。

## 0.8.0（给陪伴开一个公开出图接口）

- 新增 `draw_for(umo, uid, prompt) -> bytes | None`：不接事件、不发送，只出图；
- 失败、关闭、空提示词一律返回 `None`，不抛异常；
- 供陪伴插件的「主动分享照片」调用，出图后仍会记进面板的历史。

测试 39 passed。

## 0.7.0（不绑定厂商的兼容层 + 拍照触发）

### 1. 响应解析不再只认 OpenAI

原来只认 `data[0].url` / `data[0].b64_json`，SiliconFlow 这类返回 `images[0].url` 的后端必然报
「响应里没有 data」。现在改成按**形状**摊平找图，认这些位置：

- `data[]`、`images[]`、`artifacts[]`、`results[]`、`output.urls[]`、`items[]`，嵌套几层都行；
- 每个叶子认 `url` / `image_url` / `b64_json` / `b64` / `base64` / `image_base64`，也认 data URI 和纯 base64 字符串。

### 2. 请求参数自动适配，不写死任何一家

- `request_style=auto`（默认）：先按 OpenAI 的 `size`/`n` 发；如果后端回 400/404/415/422（参数或路径不认），
  自动换成 `image_size`/`batch_size` 再试一次，成功后记住这个后端用哪套，下次直接用；
- 401 / 429 / 5xx 不会瞎换参数重试；
- 也可强制 `openai` 或 `aliases`；
- `model` 默认改为空——各家模型名不同，代码不预设；留空就不发这个字段。

### 3. 「拍张照」也能触发

- 新增 `photo_keywords`（拍张照 / 拍一张 / 自拍 / 发张照片…）；
- 去掉触发词后只剩「给我」「帮我」这类填充词的，用 `photo_default_prompt` 兜底，不再反问。

测试 37 passed（新增响应形状、参数换用、拍照触发等用例）。

## 0.6.0（生图不生效修复 + 真正接上主插件）

### 1. 生图 handler 被陪伴插件截断，根本没执行

AstrBot 同一事件按 `priority` 串行跑 handler，且每个 handler 前判 `event.is_stopped()`。
陪伴会把「这次不回」的私聊 `stop_event()`，生图原来 priority=0，排在后面就永远不执行。

- `on_message` 改为 `priority=100`，稳定排在陪伴前面；
- 图发出后（含「想画什么」和失败话术）主动 `stop_event()`，避免陪伴/LLM 再补一条造成双回复。

### 2. 读上下文参数全错，TypeError 被静默吞掉

数据插件的真实签名是 `messages_since(bot_id, uid, since_ts, limit)` /
`recall_messages(bot_id, query, umo, uid, limit)`，生图原来把 `umo` 当第一个位置参数、
又用了不存在的 `since=`，每次都在 `except` 里被吞掉，上下文永远是空。

- 按真实签名传参；
- 新增 `_companion()` / `_bot_id()`：从陪伴读 `data_bot_id`，保证读的是同一份记忆库；
- `_star_instance` 增加 `require_mem`，能找到不带 `mem` 的陪伴主插件。

### 说明

这次建立的真实联系只有「同一 bot_id + 同一份记忆」。人物外观仍来自本插件自己的
`character_appearance`；陪伴侧目前没有外观字段，硬塞 `persona_extra` 会变成整段人格描述。
要做「陪伴主动发图 / 按心流配图」，需要在陪伴侧加明确的接口，不能靠猜。

测试 31 passed（新增 stop_event、bot_id 两条用例）。

## 0.5.1

- 版本号规则改为：测试版动末位、发布版动第二位、大版本动首位；不再限制奇偶与 1、7；
- `check_version.py` 同步放宽；
- 功能无变化。

## 0.5.0

- 图标定稿为手写体版（两个人剪影 + 心口 `Amo`），跟陪伴、数据同一张；
- 与 0.4.8 图相同，按「替换文件必须升版本」再发一次。

## 0.4.8

- 图标换成品牌图：两个人剪影，心口各一个手写 `Amo`（和陪伴、数据同一张）；

## 0.4.6

- `repo` 统一成主仓库规范写法 `https://github.com/GM888888456/Astrbot_Amo_Image/`；
- 补 `tools/check_version.py` 的仓库地址校验（格式不对直接失败）。

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
