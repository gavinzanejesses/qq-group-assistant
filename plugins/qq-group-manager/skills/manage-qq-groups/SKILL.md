---
name: manage-qq-groups
description: Configure, deploy, diagnose, and maintain the QQ Group Assistant software over OneBot 11. Use when Codex needs to translate natural-language QQ group requirements into YAML policies, start or inspect the local bot, validate schedules and regex rules, review audit logs, troubleshoot NapCat/NoneBot connectivity, or perform explicitly authorized group-management workflows such as reminders, admissions, recalls, mutes, and removals.
---

# Manage QQ Groups

Treat the software as the execution layer and this Skill as the configuration and operations layer.

## Locate and inspect

1. Locate `pyproject.toml`, `config.example.yml`, `bot.py`, and `qq_group_assistant.py`.
2. Read the active `config.yml` without exposing QQ numbers, tokens, member lists, or message logs in public output.
3. Run `qq-group-assistant validate --config <path>` before starting or restarting.
4. Use `qq-group-assistant doctor --config <path>` and runtime logs to verify OneBot connectivity.

Read [references/configuration.md](references/configuration.md) when editing policies. Read [references/safety.md](references/safety.md) before enabling destructive actions or publishing the project.

## Translate user requirements

Map concrete requirements into narrowly scoped configuration:

- reminder cadence and quiet hours -> `remark.cron` and `remark.remind_cooldown_hours`;
- accepted card formats -> `remark.pattern` and `remark.example`;
- application decisions -> `join_review.deny_patterns` and `approve_patterns`;
- message governance -> `moderation.rules`, `allow_patterns`, and `dry_run`;
- scam exceptions -> `scam_protection.trusted_group_ids`;
- trusted operators -> `admin_commands.authorized_users`;
- question answering -> `ai_qa` plus a verified `knowledge.md`;
- announcements -> `public_account_reminder`.
- multiple personalized announcements -> `scheduled_pushes`;
- simple blocked terms -> `moderation.blocked_words`, with regex rules for advanced matching;
- reminder wording -> `remark.group_message_template` and `private_message_template`.

Prefer deterministic rules for group operations. Use an LLM for answering questions or proposing policies, not as an unrestricted authority to kick, mute, approve, or delete.

## Apply changes

1. Preserve the user's active config and unrelated edits.
2. Change the smallest relevant fields.
3. Validate the config and compile changed Python files.
4. Restart only the assistant processes in scope.
5. Confirm `Bot <id> connected` in the log.
6. Verify scheduled or requested actions in `data/audit.jsonl`; never infer success from process existence alone.

Use `scripts/manage.py` for portable validation, diagnosis, or audit summaries when the console entry point is unavailable.

## Authorization rules

- Read-only inspection, validation, and diagnostics may run without confirmation.
- Starting or restarting the local bot is allowed when the user asks to start, deploy, or apply configuration.
- Sending announcements, approving applications, recalling messages, muting, changing cards, or removing members changes external state. Execute only when explicitly requested or when an already-approved policy clearly authorizes the exact operation.
- Before bulk removal, refresh the member list and skip compliant cards, absent members, owners, and administrators.
- Keep dry-run defaults for new moderation rules and report uncertainty instead of claiming success.

## Publish

Before GitHub or marketplace publication:

1. Ensure `.env`, `config.yml`, `data/`, QR images, knowledge bases, cookies, logs, and member exports are ignored.
2. Search tracked files for real QQ numbers, API keys, access tokens, private messages, and local absolute paths.
3. Run the app tests, Skill validator, and plugin validator.
4. State clearly that OneBot/NapCat is unofficial and may trigger QQ risk controls.
5. Do not publish or create a remote repository until the user authorizes the destination and repository visibility.
