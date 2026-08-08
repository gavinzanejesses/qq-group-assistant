from __future__ import annotations

import argparse
import json
import os
import re
import socket
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

REQUIRED_SECTIONS = {"remark", "join_review", "moderation", "admin_commands"}
PATTERN_LOCATIONS = (
    ("remark", "pattern"),
    ("join_review", "deny_patterns"),
    ("join_review", "approve_patterns"),
    ("moderation", "allow_patterns"),
    ("scam_protection", "patterns"),
    ("scam_protection", "suspicious_patterns"),
)


def read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("配置根节点必须是 YAML 对象")
    return data


def iter_patterns(config: dict[str, Any]):
    for section, key in PATTERN_LOCATIONS:
        value = config.get(section, {}).get(key)
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        for index, pattern in enumerate(values):
            yield f"{section}.{key}[{index}]", pattern
    for index, rule in enumerate(config.get("moderation", {}).get("rules", [])):
        yield f"moderation.rules[{index}].pattern", rule.get("pattern")


def validate_config(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        config = read_yaml(path)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        return [str(exc)]
    if not isinstance(config.get("group_id"), int):
        errors.append("group_id 必须是整数")
    missing = sorted(REQUIRED_SECTIONS - config.keys())
    if missing:
        errors.append("缺少配置段: " + ", ".join(missing))
    authorized = config.get("admin_commands", {}).get("authorized_users", [])
    if not authorized:
        errors.append("admin_commands.authorized_users 不能为空")
    for location, pattern in iter_patterns(config):
        if not isinstance(pattern, str):
            errors.append(f"{location} 必须是字符串")
            continue
        try:
            re.compile(pattern)
        except re.error as exc:
            errors.append(f"{location} 正则无效: {exc}")
    for section in ("remark", "activity_ranking", "public_account_reminder"):
        cron = config.get(section, {}).get("cron")
        if cron is not None and (not isinstance(cron, str) or len(cron.split()) != 5):
            errors.append(f"{section}.cron 必须是五段 cron 表达式")
    for index, push in enumerate(config.get("scheduled_pushes", [])):
        cron = push.get("cron") if isinstance(push, dict) else None
        if not isinstance(cron, str) or len(cron.split()) != 5:
            errors.append(f"scheduled_pushes[{index}].cron 必须是五段 cron 表达式")
    remark = config.get("remark", {})
    group_template = remark.get("group_message_template", "{mentions}")
    private_template = remark.get("private_message_template", "{total}{example}")
    try:
        rendered = group_template.format(
            current=1,
            total=3,
            mentions="{mentions}",
            example=remark.get("example", "示例"),
        )
        private_template.format(total=3, example=remark.get("example", "示例"))
        if "{mentions}" not in rendered:
            errors.append("remark.group_message_template 必须包含 {mentions}")
    except (KeyError, ValueError) as exc:
        errors.append(f"名片提醒模板变量无效: {exc}")
    promotion = config.get("promotion_host", {})
    if promotion.get("enabled") and not promotion.get("event_date"):
        errors.append("启用宣传主持前必须设置活动日期")
    event_date = promotion.get("event_date")
    if event_date:
        try:
            datetime.strptime(event_date, "%Y-%m-%d")
        except (TypeError, ValueError):
            errors.append("promotion_host.event_date 必须是有效的 YYYY-MM-DD 日期")
    slot_ids: list[str] = []
    for index, slot in enumerate(promotion.get("slots", [])):
        if not isinstance(slot, dict):
            errors.append(f"promotion_host.slots[{index}] 必须是对象")
            continue
        slot_ids.append(str(slot.get("id", "")))
        try:
            start = datetime.strptime(str(slot.get("start_time", "")), "%H:%M")
            end = datetime.strptime(str(slot.get("end_time", "")), "%H:%M")
            if end <= start:
                errors.append(f"promotion_host.slots[{index}] 结束时间必须晚于开始时间")
        except ValueError:
            errors.append(f"promotion_host.slots[{index}] 时间必须使用 HH:MM 格式")
    if len(slot_ids) != len(set(slot_ids)):
        errors.append("promotion_host.slots 的 id 不能重复")
    try:
        promotion.get("message_template", "{department}{content}").format(
            department="示例社团",
            start_time="14:00",
            end_time="14:10",
            content="示例内容",
        )
    except (KeyError, ValueError) as exc:
        errors.append(f"宣传主持模板变量无效: {exc}")
    return errors


def command_validate(args: argparse.Namespace) -> int:
    path = Path(args.config).resolve()
    if not path.exists():
        print(f"ERROR: 配置不存在: {path}")
        return 2
    errors = validate_config(path)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"OK: 配置有效: {path}")
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    config = Path(args.config).resolve()
    failures = validate_config(config) if config.exists() else [f"配置不存在: {config}"]
    print("config:", "ok" if not failures else "failed")
    for failure in failures:
        print(" -", failure)
    key_present = bool(os.getenv("ZHIPU_API_KEY"))
    print("zhipu_api_key:", "configured" if key_present else "not configured (AI QA unavailable)")
    with socket.socket() as sock:
        sock.settimeout(0.5)
        onebot_up = sock.connect_ex((args.host, args.port)) == 0
    print(f"onebot_listener({args.host}:{args.port}):", "reachable" if onebot_up else "not reachable")
    return 1 if failures else 0


def command_audit_summary(args: argparse.Namespace) -> int:
    path = Path(args.audit).resolve()
    if not path.exists():
        print(f"ERROR: 审计日志不存在: {path}")
        return 2
    counts: Counter[str] = Counter()
    bad_lines = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            if not args.date or str(record.get("time", "")).startswith(args.date):
                counts[str(record.get("action", "unknown"))] += 1
        except json.JSONDecodeError:
            bad_lines += 1
    for action, count in counts.most_common():
        print(f"{action}: {count}")
    if bad_lines:
        print(f"invalid_json_lines: {bad_lines}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QQ Group Assistant operations CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate YAML configuration")
    validate.add_argument("--config", default="config.yml")
    validate.set_defaults(func=command_validate)
    doctor = sub.add_parser("doctor", help="run local health checks")
    doctor.add_argument("--config", default="config.yml")
    doctor.add_argument("--host", default="127.0.0.1")
    doctor.add_argument("--port", type=int, default=8080)
    doctor.set_defaults(func=command_doctor)
    audit = sub.add_parser("audit-summary", help="summarize audit actions")
    audit.add_argument("--audit", default="data/audit.jsonl")
    audit.add_argument("--date", help="ISO date prefix, for example 2026-08-03")
    audit.set_defaults(func=command_audit_summary)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
