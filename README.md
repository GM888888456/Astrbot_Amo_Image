# 我想和你度日常（Astrbot_Amo_Image）

<img src="logo.png" width="120" alt="图标">

> 反馈、Bug、建议请加 QQ 群：**1055679275**
>
> 配套插件：[Astrbot_Amo_forever](https://github.com/GM888888456/Astrbot_Amo_forever)（陪伴）、[Astrbot_Amo_data](https://github.com/GM888888456/Astrbot_Amo_data)（数据）

给 Amo 做生图联动的第三个插件：聊天里说一句「画一张……」，她按人设把画面画出来。
它只负责画图，不参与聊天判定、不改 AstrBot 核心链路。

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
| `model` | `gpt-image-1` | 后端支持的模型名 |
| `size` | `1024x1024` | 尺寸 |
| `n` | `1` | 每次张数 |
| `quality` / `style` | 空 | 留空就不发这个字段，兼容不支持的旧后端 |
| `timeout` | `90` | 生图超时（秒） |
| `character_appearance` | 空 | 人物外观描述 |
| `use_memory_context` | `true` | 是否用数据插件补上下文 |
| `memory_context_limit` | `6` | 取几条对话 |
| `allow_private` / `allow_group` | `true` | 私聊 / 群聊是否可用 |
| `require_at_in_group` | `false` | 群里必须 @ 才画 |
| `quote_in_group` | `true` | 群里发图时引用原消息；私聊永远不引用 |
| `keywords` | 画一张 / 画个 / 帮我画 / 生成图片 / 来张图 / 画张 | 触发词 |
| `cooldown_seconds` | `30` | 同一人冷却 |
| `daily_limit` | `20` | 每人每天次数，`0` 不限 |
| `reply_prefix` | 空 | 发图前带一句话 |
| `error_reply` | 画不出来，等会儿再试试 | 失败话术 |

## 用法

- 私聊：`画一张 下雨天的便利店门口`
- 群聊：`@机器人 画一张 她在天台看晚霞`
- 命令：`/amoimage status` 看后端与今日额度；`/amoimage draw <提示词>` 手动出图

## 后端兼容

- 支持 `data[0].url` 与 `data[0].b64_json` 两种返回；`url` 模式会先把图片下载成字节再发，避免平台裂图；
- `url` 模式若下载失败、`b64` 解析失败、接口 4xx/5xx、超时，统一降到 `error_reply`；
- one-api / new-api / 自建 SD 兼容层只要实现了 `/images/generations` 就能用。

## 面板

页面在 `views/dashboard/`，AstrBot 会自动扫描并通过 iframe 加载：

| 接口 | 方法 | 用途 |
|---|---|---|
| `/Astrbot_Amo_Image/status` | GET | 后端、模型、今日用量、累计 |
| `/Astrbot_Amo_Image/history` | GET | 最近 40 次生图 |
| `/Astrbot_Amo_Image/config` | GET | 当前配置（密钥只回掩码） |
| `/Astrbot_Amo_Image/test` | POST | 试画，返回 `data:image/png;base64` |

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

和另外两个插件一致：`metadata.yaml` / `CHANGELOG.md` / `README.md` / `main.py` 四处一致；
尾号取偶数，版本号里不出现数字 `1` 和 `7`。当前 **0.4.4**。
