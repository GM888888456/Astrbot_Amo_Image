# 我想永远陪着你 · 相片（Astrbot_Amo_Image）

<img src="logo.png" width="120" alt="图标">

> 反馈、Bug、建议请加 QQ 群：**1055679275**
>
> 配套插件：[Astrbot_Amo_forever](https://github.com/GM888888456/Astrbot_Amo_forever)（陪伴）、[Astrbot_Amo_data](https://github.com/GM888888456/Astrbot_Amo_data)（数据）

给 Amo 做生图联动的第三个插件：聊天里说一句「画一张……」，她按人设把画面画出来。
它只负责画图，不参与聊天判定、不改 AstrBot 核心链路。

## 1.8.3（包初始化兼容单测/CI）

- `__init__.py` 改成「先相对导入，失败退回 `from main import …`」：非包方式加载（pytest/CI 直接收集）时不再报 `attempted relative import with no known parent package`。
- 生图业务逻辑无改动。

## 1.8.2（环境跟着地点走，不再随机室内）

- `draw_for(place, activity, zone)` / `_final_prompt(place, activity, zone)` / `_remember_scene(zone)`：Amo 给的这一处地点和场景类型是唯一真相，换地点后不许沿用上一张的场景。
- `match_scene(place, zone)` + `ZONE_SCENES`：按日程的 hotel/scenic/home/street… 直接定场景，景点名字里没有「景区」两个字也不会被画成室内（「恐龙遗址园」这类）。
- 场景表补「酒店/宾馆/民宿/客房」；`scene_anchor` 优先级修正为先认调用方解析好的场景，不再被地点重算覆盖。
- 家补「小窝/出租屋」别名。

## 1.8.1（自拍判定听 Amo 的）

- `draw_for(..., selfie=None)` / `_final_prompt(..., selfie=)` / `_camera(forced=)` / `_is_selfie(forced=)`：陪伴插件给的判定优先，不再去扫整段拼好的提示词。
- 修：「在景区/老街/小店」这些地点词以前会把自拍翻成第三人称（路人举手机拍），现在 Amo 说是自拍就一定画自拍。

## 1.8.0（挂载模式：只当执行者）

- 新增 `mount_only`（默认开）：陪伴插件 Amo 在的时候，本插件不再自己认关键词、不 `stop_event`、不自己发消息，要不要图全听 Amo 的；Amo 没装或被关掉时才回到独立模式。
- 新增执行者入口 `change_outfit_for()`：Amo 下发换装，插件只负责换一套并返回造型描述，发不发、怎么说都由 Amo 决定。
- 收到第一条消息时打一行 `[amo_image] 挂载模式：…`，一眼看出判定归谁。

## 1.7.8（自拍默认不拿奶茶/食物）

- 提示词没写正在吃喝时，`SELFIE_POSE` 明确禁止手里拿饮料杯、奶茶杯、咖啡杯或食物；`NO_PROP_CLAUSE` 再补一刀，条件负面词也加 `milk tea / cup / food`。
- 只有提示词明确写了「喝 / 奶茶 / 吃 / 饭 …」才用 `SELFIE_POSE_WITH_PROP` 允许拿饮料/食物。
- 修：上一版为了兼容手里有饮料，把姿势写成「另一只手可以拿饮料」，结果每张自拍都塞一杯奶茶。

## 1.7.7（自拍提示前置，禁止“别人拿手机拍”）

- `build_prompt(selfie=True)` 在提示词最前面加【这是一张自拍】：手机前置、近景半身、直视镜头、画面里只有她一个人、没有摄影师、不是别人站在对面拿手机拍。
- `SELFIE_CAMERA` / `SELFIE_POSE` 加强：第一人称自拍视角、镜头在她手里、另一只手可以拿饮料/拎袋子；明确不要全身、背影、看别处。

## 1.7.6（面板配色与终端统一）

- 底色、面板、控件底统一成同一个颜色（浅色 #f4f4f6 / 深色 #1e1e22），卡片只留 1px 描边。
- 强调色与信息色对齐终端：pink #e8489a、ok #0284c7（深色 #38bdf8）。

## 1.7.5（配置自愈 + 数据目录修正）

- 同其它插件接入 `config_guard`；顺带修正 `_plugin_data_dir()`——少上跳一级，`look_state/face_lock/scene_lock` 以前会写到 `plugins/plugin_data/` 下面。

## 1.7.4（失败不扣额度）

- 出图失败不再扣每日额度、也不占冷却：只有真的拿到图才 `_mark_call()`；
- 批量出图同理：整批都失败就不计数；
- 新增 2 条用例（失败不扣、成功扣 1）。

## 1.7.3（面板读不到数据的真正原因）

- 根因：AstrBot 把 `bridge-sdk.js` 注入在 `app.js` **之后**（实测页面里 56 行是 app.js、
  57 行才是 SDK）。这三个面板在脚本开头就 `const bridge = window.AstrBotPluginView`，
  那一刻全局还不存在，于是 `bridge` 永远是 `null`——所有 `get()` 都返回空，
  面板自然「读取不到」，而语音面板是每次调用重新找桥接，所以它一直正常；
- 改成：每次请求都 `resolveBridge()` 重新解析；启动时 `waitForBridge()` 最多等 6 秒再加载；
- 用 Node 模拟「先执行脚本、后注入 SDK」验证：注入前返回 null 不抛错，注入后正常拿到数据。

## 1.7.2（面板：连接状态失败能自己说明白）

- `app.js` 引用加版本号（`?v=1.7.2`），升级后不会拿到旧脚本；
- 连接失败时写明原因、可点重试。

## 1.7.1（连接状态别一打开就报失败）

- 修：面板脚本比桥接先跑，头一次取 `health` 拿不到，顶部会显示失败；改成重试 8 秒；
- `renderStatus` 拿到状态后顺手画一次连接状态条。

## 1.7.0（连接状态 + 额度归属）

- 新增 `link.py` + `/health`，面板顶部显示终端/记忆库连接状态；
- 新增 `quota()`：**每日出图张数只在这里记**，终端不再自己存一份，
  否则同一张图会被数两遍；
- 服装库仍然只有一份（在终端的 `wardrobe.json`，本插件只做面板代理），
  人物参考图仍以终端模板为准，本插件不另存。


## 1.6.3（改名）

- 改名「我想永远陪着你-相片」，界面文案同步。

## 1.6.2 一致性：同一张脸、同一个环境、同一个姿势

- 地点固定环境（九类场景锚点），同一地点永远同一段背景描述，招牌/陈设/光线都不换；
- 第一次出图后冻结外观到 `face_lock.json`，之后改配置也不影响已经定下的脸；
- 自拍：一只手举手机但不入镜、不许抱臂；他拍：不出现拍摄者和手机；
- 兜底话术换成「等我一下，晚点拍给你」这类人话，不再说「画不出来」；
- 一条指令写多个提示词（换行 / 分号 / 竖线），一次画完合成一条消息发出。

## 1.6.1 持久化路径合规

- 手动换装记录 `look_state.json` 从插件安装目录改到 `data/plugin_data/<插件名>/`（上架审核要求）；
- 新增 `_plugin_data_dir()`：优先用上下文给的插件数据目录，拿不到再拼 `plugin_dir/../plugin_data/<插件名>`。

## 1.6.0 面板服装库

- 发型固定（`character_hairstyle`）；服装按天固定（`character_outfits`，日程里换衣服才换套）；
- 换装有冷却（`outfit_change_cooldown_minutes`，默认 60 分钟），连着的两条换装日程只算一次；
- 服装池留空用内置保底池，不会出现没写服装的提示词；
- 支持负面提示词（`negative_prompt`），后端不认会自动去掉重试；
- 发「换一套衣服」或 `/amoimage outfit` 手动换下一套；
- 私聊不限量（`private_daily_limit=0`），群聊每群每天 20 张（`group_daily_limit`）；
- 群聊必须真正 @ 机器人，唤醒词不再算；
- `safe_mode` 分级内容边界，`draw_for` 和记忆上下文同样受限；
- 新增 `provider`：openai / openai_chat / gemini / custom，一套配置换后端；
- 面板重做成四个标签页：概览、配置（可保存）、试画、记录；
- 提示词补上性别锚、角色名和日程里的「正在干嘛」，不再画成男生、不再和日程对不上；
- 修复面板「保存配置」一直失败：宿主会把 `{code,data}` 解包成 `data`，前端已兼容两种形状；
- 新增「从人格提取」：读陪伴的人格设定，自动填性别、外观、角色名；
- 0.13.1：修面板读不到外部配置里 api_base / key 的问题（掩码字段名 + 资产缓存版本号）；
- 0.13.2：从人格提取时连年龄、身高、体重、体型一起带进外观；
- 0.13.3：拍摄角度分开——拍自己用前置自拍，拍别人/别的东西用第三人称他拍；
- 0.14.0：服装改成读陪伴日程里的 `outfit`，日程换一套衣服，图里就换一套；
- 0.15.0：模型改成从后端 `/models` 拉取后选择；
- 0.15.1：列表里没有图片模型时，保留当前模型并可手填一次；
- 0.16.0：面板「试画」换成「服装」，可以直接添衣服、指定今天穿哪件。


## 0.8.0 公开出图接口

- 新增 `draw_for(umo, uid, prompt)`，给陪伴的「主动分享照片」用；
- 只出图不发送，失败返回 `None`。


## 0.7.0 不绑定厂商的兼容层

- 响应解析按形状找图：`data[]` / `images[]` / `artifacts[]` / `output.urls[]` 都认，不再只认 OpenAI；
- 参数名自动适配：默认先发 `size`/`n`，400 时自动换 `image_size`/`batch_size` 并记住；
- `model` 不再预设，填后端自己的模型名即可；
- 新增「拍张照 / 自拍」触发词，没具体描述时用默认提示词。

## 0.6.0 生图不生效修复

- 生图 handler 提到 `priority=100`，不再被陪伴的 `stop_event` 截断；
- 出图后主动停事件，避免双回复；
- 读记忆改用数据插件真实签名，并从陪伴读 `data_bot_id`，上下文不再为空；
- 外观仍用本插件 `character_appearance`，陪伴侧还没有外观字段。


## 0.5.1 版本号规则

- 测试版动末位、发布版动第二位、大版本动首位；
- 不再限制奇偶与数字 1、7。


## 0.5.0 图标定稿

- 图标与陪伴、数据统一；
- 本次仅为替换文件后的版本推进。


## 0.4.8 换新图标

- 图标换成品牌图：两个人剪影，心口各一个手写 `Amo`；
- 与陪伴、数据两个插件共用同一张。


## 0.4.6 仓库地址规范

- 插件 `repo` 固定为 `https://github.com/GM888888456/Astrbot_Amo_Image/`；
- `check_version.py` 一并校验，指错就发版失败。


## 它和另外两个插件怎么联动

| 联动点 | 来源 | 行为 |
|---|---|---|
| 人物外观 | 本插件 `character_appearance` | 拼在提示词最前面，保证每次是同一个人 |
| 剧情上下文 | 数据插件（`Astr_Amo_data`） | 读最近几句对话补进提示词，让画面接得上剧情 |
| 人设化失败话术 | 本插件 `error_reply` | 后端挂了也只回一句人设话，不抛异常 |

数据插件没装、没启用、接口报错，都会**静默跳过**，生图照样能画——只是少了上下文。

## 安装

1. 把 `Astrbot_Amo_Image` 目录放进 AstrBot 的 `data/plugins/`；
2. 重启 AstrBot，在 WebUI 的插件页启用；
3. 在插件配置里填 `api_base` / `api_key` / `model`，至少这三项。

## 配置

| 配置项 | 默认 | 说明 |
|---|---|---|
| `enable` | `true` | 总开关 |
| `api_base` | `https://api.openai.com/v1` | OpenAI 兼容根地址，请求 `{api_base}/images/generations` |
| `api_key` | 空 | 只存本机配置，不会写进日志 |
| `model` | 空 | 后端支持的模型名；留空用后端默认 |
| `size` | `1024x1024` | 尺寸 |
| `n` | `1` | 每次张数 |
| `quality` / `style` | 空 | 留空就不发这个字段，兼容不支持的旧后端 |
| `timeout` | `90` | 生图超时（秒） |
| `character_appearance` | 空 | 人物外观描述 |
| `use_memory_context` | `true` | 是否用数据插件补上下文 |
| `memory_context_limit` | `6` | 取几条对话 |
| `allow_private` / `allow_group` | `true` | 私聊 / 群聊是否可用 |
| `require_at_in_group` | `true` | 群里必须真 @ 机器人才画（唤醒词不算） |
| `quote_in_group` | `true` | 群里发图时引用原消息；私聊永远不引用 |
| `keywords` | 画一张 / 画个 / 帮我画 / 生成图片 / 来张图 / 画张 | 触发词 |
| `cooldown_seconds` | `30` | 同一人冷却 |
| `private_daily_limit` | `0` | 私聊每人每天次数，`0` 不限 |
| `group_daily_limit` | `20` | 每个群每天次数，`0` 不限 |
| `safe_mode` | `true` | 内容边界：私聊拦露骨裸体/性器官/性行为/未成年相关，群聊额外拦只穿内衣/比基尼 |
| `blocked_reply` | 这个画不了，换个描述吧 | 命中内容边界时的回复 |
| `reply_prefix` | 空 | 发图前带一句话 |
| `error_reply` | 画不出来，等会儿再试试 | 失败话术 |

## 内容边界

`safe_mode` 打开时：

- 私聊：只拦露骨裸体、性器官、性行为、未成年相关；比基尼、内衣这类日常向描述放行；
- 群聊：额外拦「只穿内衣 / 内衣照 / 比基尼 / 情趣内衣」；
- 记忆上下文、`draw_for` 分享入口走同一套判定，群友往聊天里塞「画裸体」不会顺着拼进提示词；
- 提示词注入词（「忽略之前」「system prompt」「越狱」）一律拦。

这是一层关键词 + 正则防线，挡不住谐音、外语、拆字和极端变体；要更稳需要图片返回后加 NSFW 输出检查，
插件不内置这类模型，避免偷跑第三方服务。

「必须真 @」依赖消息里的 `At` 组件。QQ 没问题；Lark / Telegram 这类平台如果 mention 不走 `At`，
可能出现「@ 了也不触发」，到时候要按平台补一个判定分支。

## 用法

- 私聊：`画一张 下雨天的便利店门口`
- 群聊：`@机器人 画一张 她在天台看晚霞`（必须真 @，唤醒词不算）
- 命令：`/amoimage status` 看后端与今日额度；`/amoimage draw <提示词>` 手动出图

## 后端兼容

- `provider=openai`：`/images/generations`，认 `data[].url`、`data[].b64_json`、`images[].url`；
- `provider=openai_chat`：`/chat/completions`，认 markdown 图片链接和 `message.images`；
- `provider=gemini`：原生 `generateContent`，认 `candidates[].content.parts[].inlineData`；
- `provider=custom`：完整 URL + 请求体模板 + 响应路径，接非标准端点；
- `url` 模式会先把图片下载成字节再发，避免平台裂图；
- `url` 模式若下载失败、`b64` 解析失败、接口 4xx/5xx、超时，统一降到 `error_reply`；
- one-api / new-api / 自建 SD 兼容层只要实现了 `/images/generations` 就能用。

## 面板

页面在 `views/dashboard/`，AstrBot 会自动扫描并通过 iframe 加载：

| 接口 | 方法 | 用途 |
|---|---|---|
| `/Astrbot_Amo_Image/status` | GET | 后端、模型、今日用量、累计 |
| `/Astrbot_Amo_Image/history` | GET | 最近 40 次生图 |
| `/Astrbot_Amo_Image/config` | GET | 当前配置（密钥只回掩码） |
| `/Astrbot_Amo_Image/models` | GET | 拉后端模型列表 |
| `/Astrbot_Amo_Image/persona/extract` | POST | 从人格提取性别/外观/角色名 |
| `/Astrbot_Amo_Image/wardrobe` | GET | 服装库（数据在陪伴插件，未安装时提示） |
| `/Astrbot_Amo_Image/wardrobe/save` | POST | 添加/修改一件衣服 |
| `/Astrbot_Amo_Image/wardrobe/delete` | POST | 删除一件 |
| `/Astrbot_Amo_Image/wardrobe/wear` | POST | 让她今天穿这件 |
| `/Astrbot_Amo_Image/test` | POST | 试画（面板已不提供入口，保留给调试） |

## 测试

```bash
python3 -m pytest -q     # 29 passed，不连网、不依赖 AstrBot
```

## 0.4.4 可视化面板

- AstrBot WebUI → 插件 → 我想和你度日常 → 「面板」；
- 看后端状态、今日用量、累计次数、最近 40 次记录；
- **试画一张**：面板里直接出图预览，不占聊天额度；
- `api_key` 只显示掩码，面板拿不到完整密钥。

## 0.4.2 改名与图标

- 显示名从「我想永远陪伴你·生图」改为 **我想和你度日常**，插件 ID 仍是 `Astrbot_Amo_Image`；
- 图标换成陪伴插件同款 `logo.png`，三个插件站在一起是一套；
- 功能不变，仍是 24 passed。

## 版本号规则

- 三段：`x.y.z`，**第二位和第三位只能是 0-9，逢十进一**：3.8.9 → 3.9.0，3.9.9 → 4.0.0；
- **首位不做限制**，可以是多位数：9.9.9 再进一位是 10.0.0（不是 1.0.0.0）；
- **测试版**：动第三位（7.4.0 → 7.4.1）；
- **发布版**：动第二位、第三位归零（7.4.1 → 7.5.0）；
- **大版本**：首位 +1（7.5.0 → 8.0.0）；
- `python3 tools/check_version.py` 校验四处一致与规则，
  `python3 tools/check_version.py --next test|release|major` 直接算下一个号。
