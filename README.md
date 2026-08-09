# QQ Group Assistant

一个基于 NoneBot 2 与 OneBot 11 的可配置 QQ 群管理助手。它把群规写成 YAML 策略，支持群名片提醒、入群审核、内容治理、反诈骗、定时通知、授权管理命令、智能问答和可追溯审计。

> 本项目不是 QQ 官方机器人。NapCat 等非官方客户端实现可能触发账号风控，也可能因 QQ 升级而失效。请使用专用小号、最小权限和人工复核。

## 功能

- 按可视化时间计划检查群名片，分批 @、累计提醒次数、第三次私信并生成待清理名单；
- 用可视化字段模板自动通过、拒绝或转人工处理入群申请；
- 对广告、诈骗、辱骂等规则执行观察、撤回、禁言并记录审计日志；
- 仅允许 QQ 白名单中的管理员执行踢人、禁言、撤回、全员禁言和改名片；
- 被 @ 时接入智谱 API 回答群内知识问题，带冷却、长度限制和提示注入防护；
- 定时发送文字、公告、二维码或多张图片；
- 按活动流程充当宣传主持人，依次介绍社团、协会或部门；
- 支持多群运行、白名单群号和干运行模式；
- 提供配置验证、运行诊断和审计汇总 CLI；
- 附带 Codex 插件 Skill，可用自然语言配置、部署和排障。

## 下载后快速使用（Windows）

需要 Windows 10/11、Python 3.11+ 和一个专用 QQ 小号。

1. 在 GitHub 页面点击 **Releases** 下载发布压缩包，或点击 **Code → Download ZIP** 并解压。
2. 双击 `安装.bat`。安装向导会创建 Python 环境、安装依赖，并询问目标群号、管理员 QQ、可选的智谱 API Key，以及 NapCat 的 `launcher-user.bat` 路径。
3. 从 [NapCatQQ Releases](https://github.com/NapNeko/NapCatQQ/releases) 安装并登录 NapCat。按照 [NapCat 接入 NoneBot 官方说明](https://napneko.github.io/use/integration) 添加一个 WebSocket 客户端（反向 WS）：

   ```text
   ws://127.0.0.1:8080/onebot/v11/ws
   ```

4. 双击 `启动机器人.bat`。脚本会依次启动机器人服务和 NapCat，自动识别已有的机器人 QQ，等待反向 WebSocket 真正连接后再报告在线，并打开管理后台。
5. 页面要求令牌时直接粘贴：启动脚本已经把令牌复制到了剪贴板。

机器人账号必须加入目标群。自动审核、撤回、禁言和移出成员通常要求机器人具有群管理员权限。

> NapCat 是独立的第三方 OneBot 实现，本仓库不捆绑 QQ 或 NapCat。NapCat 的安装方式和兼容版本请以其官方文档为准。

### 手动安装

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item config.example.yml config.yml
Copy-Item .env.example .env
.\.venv\Scripts\python qq_assistant_cli.py validate --config config.yml
.\.venv\Scripts\python bot.py
```

然后修改 `config.yml` 中的群号和管理员 QQ，并在 `.env` 中设置至少 16 位的 `QQ_ASSISTANT_WEB_TOKEN`。OneBot 反向 WebSocket 地址仍为：

```text
ws://127.0.0.1:8080/onebot/v11/ws
```

## 配置与运维

编辑 `config.yml`。真实群号、管理员 QQ、API 密钥、群成员数据和日志均不应提交。

```powershell
# 验证 YAML、正则和 cron 基本结构
qq-group-assistant validate --config config.yml

# 检查配置、AI 密钥和 OneBot 监听端口
qq-group-assistant doctor --config config.yml

# 汇总某天的审计动作
qq-group-assistant audit-summary --date 2026-08-03
```

## 可视化管理后台

机器人启动后访问 `http://127.0.0.1:8080/qq-admin`。管理令牌配置在 `.env` 的 `QQ_ASSISTANT_WEB_TOKEN` 中。Windows 用户也可以运行 `scripts/open-dashboard.ps1`，脚本会把令牌复制到剪贴板并打开后台页面。

后台提供运行概览、未改名片成员、待清理名单、入群审核、定时提醒、宣传主持、内容治理、群消息和成员操作。普通用户无需编写 cron 或 JSON：可以选择“每天按间隔”“每天指定时间”“每周指定日期”，并为每条提醒配置目标群、文案和最多 9 张图片。入群审核可以自由组合字段和允许值；内容治理规则可以逐条启停并选择关键词或正则匹配；所有推送结果自动进入历史记录。

项目根目录提供 `启动机器人.bat`。双击后会校验配置、后台启动机器人和网页管理系统，并通过 `.env` 中的 `NAPCAT_START_PATH` 启动 NapCat。启动器会读取 NapCat 已有的 OneBot 配置来识别机器人 QQ，并检查管理端返回的真实在线状态；重复双击不会重复启动已有的机器人服务。

如果网页能打开但显示“机器人离线”，说明网页服务已经运行、NapCat/QQ 尚未连接。重新运行 `安装.bat`，把 NapCat 安装目录中的 `launcher-user.bat` 完整路径填入向导；首次使用还需要在弹出的 QQ 窗口中完成机器人账号登录。后续启动会优先使用已有登录状态。

“宣传主持”页面用于一次性社团、协会或部门宣传活动。管理员可设置活动日期、目标群、统一主持词，并逐行维护开始时间、结束时间、宣传部门和本环节内容。启用并保存后，机器人会在各环节开始时间自动发送主持消息；也可在明确确认后手动立即发送某一环节。

首次上线建议：

1. 使用测试群和专用小号；
2. 保持 `moderation.dry_run: true` 观察 1–3 天；
3. 先只启用非破坏性提醒；
4. 检查 `data/audit.jsonl` 的误判；
5. 再逐项开放撤回、禁言、审核或自动清理。

## 自然语言与安全边界

Codex Skill 可以把“每两小时提醒未改名片成员”“只允许两个管理员执行禁言”等需求翻译为配置或代码，但实际群操作始终由本地机器人执行。高风险操作必须经过 QQ 白名单、群权限和审计链路，不应把大模型输出直接当作无条件管理指令。

## 测试

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
```

## Codex 插件

插件位于 `plugins/qq-group-manager`，包含 `manage-qq-groups` Skill。验证：

```powershell
py C:\path\to\plugin-creator\scripts\validate_plugin.py plugins\qq-group-manager
py C:\path\to\skill-creator\scripts\quick_validate.py plugins\qq-group-manager\skills\manage-qq-groups
```

## 开源发布

项目采用 MIT License。发布前先运行 `git status --ignored`，确认 `.env`、`config.yml`、`data/`、二维码、知识库和成员 CSV 没有进入提交。

安全问题请参阅 [SECURITY.md](SECURITY.md)，贡献流程请参阅 [CONTRIBUTING.md](CONTRIBUTING.md)。
