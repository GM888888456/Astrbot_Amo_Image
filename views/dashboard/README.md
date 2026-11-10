# 面板

「我想和你度日常」的可视化面板：看后端状态、试画一张、翻最近画了什么。

## 打开方式

AstrBot WebUI → 插件 → 我想和你度日常 → 打开「面板」。

页面放在 `views/dashboard/`，由 AstrBot 以 iframe 加载；脚本通过 `window.AstrBotPluginView` bridge 调后端，不直接碰 Dashboard 的 Cookie。

## 后端接口

| 路由 | 方法 | 用途 |
|---|---|---|
| `/Astrbot_Amo_Image/status` | GET | 开关、后端、模型、今日用量、累计次数 |
| `/Astrbot_Amo_Image/history` | GET | 最近 40 次生图（时间/提示词/成败/耗时/字节） |
| `/Astrbot_Amo_Image/config` | GET | 当前配置（`api_key` 只回掩码） |
| `/Astrbot_Amo_Image/test` | POST | 试画，返回 `data:image/png;base64` |

`api_key` 只以 `sk-xx…abcd` 形式返回，面板不会显示完整密钥。
