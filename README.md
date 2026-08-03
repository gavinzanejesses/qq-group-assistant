# QQ Group Assistant

一个基于 NoneBot 2 与 OneBot 11 的可配置 QQ 群管理助手。它把群规写成 YAML 策略，支持群名片提醒、入群审核、内容治理、反诈骗、定时通知、授权管理命令、智能问答和可追溯审计。

> 本项目不是 QQ 官方机器人。NapCat 等非官方客户端实现可能触发账号风控，也可能因 QQ 升级而失效。请使用专用小号、最小权限和人工复核。

## 功能

- 按可视化时间计划检查群名片，分批 @、累计提醒次数、第三次私信并生成待清理名单；
- 根据可配置正则自动通过、拒绝或转人工处理入群申请；
- 对广告、诈骗、辱骂等规则执行观察、撤回、禁言并记录审计日志；
- 仅允许 QQ 白名单中的管理员执行踢人、禁言、撤回、全员禁言和改名片；
- 被 @ 时接入智谱 API 回答群内知识问题，带冷却、长度限制和提示注入防护；
- 定时发送公众号、公告或二维码；
- 支持多群运行、白名单群号和干运行模式；
- 提供配置验证、运行诊断和审计汇总 CLI；
- 附带 Codex 插件 Skill，可用自然语言配置、部署和排障。

## 安装

要求 Python 3.11+，以及提供 OneBot 11 反向 WebSocket 的 QQ 客户端实现。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item config.example.yml config.yml
Copy-Item .env.example .env
qq-group-assistant validate --config config.yml
python bot.py
```

在 OneBot 客户端中把反向 WebSocket 设置为：

```text
ws://127.0.0.1:8080/onebot/v11/ws
```

机器人账号必须进入目标群。审核、撤回、禁言和踢人通常要求群管理员权限。

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

后台提供详细运行概览、未改名片成员、待清理名单、立即提醒、公众号推送、群消息、禁言/解禁、成员移出和 YAML 配置校验。普通用户无需编写 cron 或 JSON：可以选择“每天按间隔”“每天指定时间”“每周指定日期”并设置时间和频率；每条推送可独立配置目标群、文案和最多 9 张图片。内容治理规则可以逐条启停并选择关键词或正则匹配，所有推送结果都会自动进入历史记录。后台默认只监听本机；不要直接暴露到公网。

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
pytest -q
ruff check .
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
