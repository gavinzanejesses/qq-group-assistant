# Safety and release checklist

## Destructive operations

For kick, mute, recall, whole-group mute, card changes, and application decisions:

1. Confirm the target group and operator authorization.
2. Refresh live member or request state.
3. Exclude owners, administrators, the bot, and protected operators where applicable.
4. Execute the exact requested operation only.
5. Verify the OneBot response or resulting event.
6. Write and inspect an audit record.

## Privacy

Never publish API keys, QQ session material, cookies, real application answers, member identifiers, message bodies, audit logs, CSV exports, QR codes, or local knowledge files. Use synthetic fixtures in tests and documentation.

## Platform boundary

NapCat and similar OneBot implementations are unofficial. Do not describe the account as a QQ official bot, guarantee uninterrupted operation, or suggest bypassing QQ risk controls.
