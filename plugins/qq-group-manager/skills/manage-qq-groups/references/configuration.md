# Configuration map

## Core switches

- `group_id`: primary group.
- `all_groups`: apply the same policies to all groups joined by the bot; default to false for new deployments.
- `admin_commands.authorized_users`: explicit operator allowlist.

## Safety-sensitive sections

- Keep `moderation.dry_run: true` until audit samples show acceptable precision.
- Keep `remark.auto_kick_enabled: false` unless the group owner explicitly authorizes automatic removal.
- Put verified official groups in `scam_protection.trusted_group_ids`; a trusted group ID bypasses group-invite punishment but does not bypass unrelated moderation rules.
- Order join review as deny, approve, then manual review. Never auto-reject unmatched applications.

## Scheduling

Use five-field cron expressions in the machine timezone. Encode quiet hours in the hour field, for example `0 8-20/2 * * *`. Validate after every edit and remember that a sleeping computer cannot execute local schedules.

Each item in `scheduled_pushes` needs a unique ID, display name, cron schedule, message, and optional group ID. Successful, failed, skipped, manual, remark, and public-account pushes are appended to `data/push_history.jsonl`.

Remark templates support `{current}`, `{total}`, `{mentions}`, and `{example}` in group messages, and `{total}` plus `{example}` in private messages. Keep `{mentions}` in the group template so targeted members are actually mentioned.

## Knowledge and AI

Keep `knowledge.md` limited to verified information. Store API keys only in `.env`. Treat member messages as untrusted prompt input and do not allow the QA model to call management operations.
