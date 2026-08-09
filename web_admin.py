from __future__ import annotations

import json
import os
import re
import secrets
import uuid
from collections import Counter
from datetime import datetime
from typing import Any, Literal

import yaml
from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Bot
from nonebot_plugin_apscheduler import scheduler
from pydantic import BaseModel, Field

from qq_group_assistant import (
    AUDIT_PATH,
    CONFIG_PATH,
    PUSH_HISTORY_PATH,
    Config,
    JoinFieldConfig,
    PromotionSlotConfig,
    Rule,
    ScheduleConfig,
    ScheduledPushConfig,
    audit,
    configure_recurring_jobs,
    load_config,
    load_pending_removals,
    publish_promotion_slot,
    publish_public_account_reminder,
    record_push,
    remind_primary_group_bad_remarks,
    rule_pattern,
    schedule_crons,
)

router = APIRouter(prefix="/qq-admin")


def require_token(x_admin_token: str = Header(default="")) -> None:
    expected = os.getenv("QQ_ASSISTANT_WEB_TOKEN", "").strip()
    if len(expected) < 16:
        raise HTTPException(503, "管理后台令牌未配置或长度不足16个字符")
    if not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(401, "管理令牌错误")


def current_bot() -> Bot:
    bots = list(get_driver().bots.values())
    if not bots:
        raise HTTPException(503, "机器人当前离线")
    return bots[0]


def require_group(cfg: Config, group_id: int) -> None:
    if group_id != cfg.group_id and not cfg.all_groups:
        raise HTTPException(403, "该群不在管理范围内")


class ConfigUpdate(BaseModel):
    yaml_text: str = Field(min_length=1, max_length=100_000)


class ActionRequest(BaseModel):
    action: Literal[
        "send_message",
        "remind_now",
        "publish_public_account",
        "mute",
        "unmute",
        "kick",
    ]
    group_id: int
    user_id: int | None = None
    duration: int | None = Field(default=None, ge=0, le=2_592_000)
    message: str | None = Field(default=None, max_length=2000)
    confirmation: str = ""


class PersonalizationUpdate(BaseModel):
    remark_enabled: bool
    remark_cron: str
    remark_schedule: ScheduleConfig
    remark_cooldown_hours: int = Field(ge=1, le=720)
    remark_batch_size: int = Field(ge=1, le=30)
    remark_example: str = Field(min_length=1, max_length=1000)
    remark_group_template: str = Field(min_length=1, max_length=4000)
    remark_private_template: str = Field(min_length=1, max_length=4000)
    public_enabled: bool
    public_cron: str
    public_schedule: ScheduleConfig
    public_message: str = Field(min_length=1, max_length=2000)
    public_image_paths: list[str] = Field(max_length=9)
    moderation_enabled: bool
    moderation_dry_run: bool
    blocked_words: list[str] = Field(max_length=500)
    moderation_rules: list[Rule] = Field(max_length=100)
    allow_patterns: list[str] = Field(max_length=100)
    scheduled_pushes: list[ScheduledPushConfig] = Field(max_length=100)


class JoinReviewUpdate(BaseModel):
    enabled: bool
    forbidden_words: list[str] = Field(max_length=100)
    auto_reject_invalid: bool = False
    reject_reason: str = Field(min_length=1, max_length=200)
    fields: list[JoinFieldConfig] = Field(min_length=1, max_length=10)


class PromotionUpdate(BaseModel):
    enabled: bool
    event_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    group_id: int | None = None
    message_template: str = Field(min_length=1, max_length=3000)
    slots: list[PromotionSlotConfig] = Field(max_length=100)


def validate_cron(value: str) -> None:
    try:
        CronTrigger.from_crontab(value)
    except ValueError as exc:
        raise HTTPException(422, f"无效 cron：{value}（{exc}）") from exc


def schedule_for_display(schedule: ScheduleConfig | None, cron: str) -> ScheduleConfig:
    if schedule is not None:
        return schedule
    parts = cron.split()
    if len(parts) == 5 and parts[0].isdigit():
        minute = int(parts[0])
        interval = re.fullmatch(r"(\d+)-(\d+)/(\d+)", parts[1])
        if interval:
            return ScheduleConfig(
                mode="daily_interval",
                start_hour=int(interval.group(1)),
                end_hour=int(interval.group(2)),
                interval_hours=int(interval.group(3)),
                minute=minute,
            )
        if re.fullmatch(r"\d+(?:,\d+)*", parts[1]) and parts[4] == "*":
            return ScheduleConfig(
                mode="daily_times",
                times=[f"{int(hour):02d}:{minute:02d}" for hour in parts[1].split(",")],
            )
    return ScheduleConfig(mode="daily_times", times=["09:00"])


def validate_templates(group_template: str, private_template: str, example: str) -> None:
    try:
        rendered = group_template.format(current=1, total=3, mentions="{mentions}", example=example)
        private_template.format(total=3, example=example)
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, f"消息模板变量无效：{exc}") from exc
    if "{mentions}" not in rendered:
        raise HTTPException(422, "群提醒模板必须包含 {mentions}")


@router.get("", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> str:
    return DASHBOARD_HTML


@router.get("/media/{asset_path:path}", include_in_schema=False)
async def dashboard_media(asset_path: str) -> FileResponse:
    assets_root = (CONFIG_PATH.parent / "assets").resolve()
    target = (assets_root / asset_path).resolve()
    if assets_root not in target.parents or not target.is_file():
        raise HTTPException(404, "图片不存在")
    return FileResponse(target)


@router.get("/api/status")
async def status(_: None = Header(default=None), x_admin_token: str = Header(default="")) -> dict[str, Any]:
    require_token(x_admin_token)
    cfg = load_config()
    bots = list(get_driver().bots.values())
    return {
        "online": bool(bots),
        "bot_id": int(bots[0].self_id) if bots else None,
        "primary_group": cfg.group_id,
        "all_groups": cfg.all_groups,
        "config_path": str(CONFIG_PATH),
        "audit_exists": AUDIT_PATH.exists(),
    }


@router.get("/api/overview")
async def overview(x_admin_token: str = Header(default="")) -> dict[str, Any]:
    require_token(x_admin_token)
    cfg = load_config()
    bot = current_bot()
    groups = await bot.get_group_list(no_cache=True)
    members = await bot.get_group_member_list(group_id=cfg.group_id, no_cache=True)
    noncompliant_count = sum(
        1
        for member in members
        if member.get("role") not in {"owner", "admin"}
        and not re.fullmatch(cfg.remark.pattern, (member.get("card") or "").strip())
    )
    today = datetime.now().astimezone().date().isoformat()
    audit_counts: Counter[str] = Counter()
    if AUDIT_PATH.exists():
        for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("time", "")).startswith(today):
                audit_counts[str(row.get("action", "unknown"))] += 1
    history: list[dict[str, Any]] = []
    if PUSH_HISTORY_PATH.exists():
        for line in PUSH_HISTORY_PATH.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("time", "")).startswith(today):
                history.append(row)
    next_jobs = [
        {
            "id": job.id,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        }
        for job in sorted(
            scheduler.get_jobs(),
            key=lambda item: item.next_run_time.timestamp()
            if item.next_run_time
            else float("inf"),
        )[:12]
    ]
    return {
        "group_count": len(groups),
        "member_count": len(members),
        "noncompliant_count": noncompliant_count,
        "pending_count": len(load_pending_removals()),
        "pushes_today": len(history),
        "push_failures_today": sum(1 for row in history if row.get("status") == "failed"),
        "moderation_hits_today": audit_counts.get("moderation_hit", 0),
        "join_approved_today": audit_counts.get("join_approved", 0),
        "join_pending_today": audit_counts.get("join_pending_manual", 0),
        "next_jobs": next_jobs,
        "recent_pushes": list(reversed(history[-5:])),
    }


@router.get("/api/config")
async def get_config(x_admin_token: str = Header(default="")) -> dict[str, str]:
    require_token(x_admin_token)
    return {"yaml_text": CONFIG_PATH.read_text(encoding="utf-8")}


@router.get("/api/personalization")
async def get_personalization(x_admin_token: str = Header(default="")) -> dict[str, Any]:
    require_token(x_admin_token)
    cfg = load_config()
    return {
        "remark_enabled": cfg.remark.enabled,
        "remark_cron": cfg.remark.cron,
        "remark_schedule": schedule_for_display(cfg.remark.schedule, cfg.remark.cron).model_dump(),
        "remark_cooldown_hours": cfg.remark.remind_cooldown_hours,
        "remark_batch_size": cfg.remark.batch_size,
        "remark_example": cfg.remark.example,
        "remark_group_template": cfg.remark.group_message_template,
        "remark_private_template": cfg.remark.private_message_template,
        "public_enabled": cfg.public_account_reminder.enabled,
        "public_cron": cfg.public_account_reminder.cron,
        "public_schedule": schedule_for_display(
            cfg.public_account_reminder.schedule,
            cfg.public_account_reminder.cron,
        ).model_dump(),
        "public_message": cfg.public_account_reminder.message,
        "public_image_paths": cfg.public_account_reminder.image_paths
        or ["assets/official_account_qr.png"],
        "moderation_enabled": cfg.moderation.enabled,
        "moderation_dry_run": cfg.moderation.dry_run,
        "blocked_words": cfg.moderation.blocked_words,
        "moderation_rules": [rule.model_dump() for rule in cfg.moderation.rules],
        "allow_patterns": cfg.moderation.allow_patterns,
        "scheduled_pushes": [push.model_dump() for push in cfg.scheduled_pushes],
    }


@router.get("/api/join-review")
async def get_join_review(x_admin_token: str = Header(default="")) -> dict[str, Any]:
    require_token(x_admin_token)
    review = load_config().join_review
    return {
        "enabled": review.enabled,
        "format_example": " ＋ ".join(field.example for field in review.fields)
        if review.fields
        else review.format_example,
        "forbidden_words": review.forbidden_words,
        "auto_reject_invalid": review.auto_reject_invalid,
        "reject_reason": review.reject_reason,
        "fields": [field.model_dump() for field in review.fields],
    }


@router.put("/api/join-review")
async def put_join_review(
    payload: JoinReviewUpdate,
    x_admin_token: str = Header(default=""),
) -> dict[str, Any]:
    require_token(x_admin_token)
    for field in payload.fields:
        if field.min_length > field.max_length:
            raise HTTPException(422, f"“{field.name}”的最短长度不能大于最长长度")
        if field.kind == "options" and not field.options:
            raise HTTPException(422, f"“{field.name}”至少需要填写一个允许值")
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    raw["join_review"].update(
        {
            "enabled": payload.enabled,
            "format_example": " ＋ ".join(field.example for field in payload.fields),
            "forbidden_words": sorted(
                {item.strip() for item in payload.forbidden_words if item.strip()}
            ),
            "auto_reject_invalid": payload.auto_reject_invalid,
            "reject_reason": payload.reject_reason.strip(),
            "fields": [field.model_dump() for field in payload.fields],
        }
    )
    Config.model_validate(raw)
    backup = CONFIG_PATH.with_suffix(".yml.bak")
    backup.write_text(CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    CONFIG_PATH.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    audit("web_admin_join_review_saved")
    return {"ok": True, "message": "入群审核设置已保存并立即生效"}


@router.get("/api/promotion-host")
async def get_promotion_host(x_admin_token: str = Header(default="")) -> dict[str, Any]:
    require_token(x_admin_token)
    host = load_config().promotion_host
    return host.model_dump()


@router.put("/api/promotion-host")
async def put_promotion_host(
    payload: PromotionUpdate,
    x_admin_token: str = Header(default=""),
) -> dict[str, Any]:
    require_token(x_admin_token)
    if payload.enabled and not payload.event_date:
        raise HTTPException(422, "启用宣传主持前，请先选择活动日期")
    slot_ids = [slot.id for slot in payload.slots]
    if len(slot_ids) != len(set(slot_ids)):
        raise HTTPException(422, "宣传环节编号重复，请刷新页面后重试")
    for slot in payload.slots:
        start = datetime.strptime(slot.start_time, "%H:%M")
        end = datetime.strptime(slot.end_time, "%H:%M")
        if end <= start:
            raise HTTPException(422, f"“{slot.department}”的结束时间必须晚于开始时间")
    try:
        payload.message_template.format(
            department="示例社团",
            start_time="14:00",
            end_time="14:10",
            content="示例宣传内容",
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, f"主持词模板中的变量有误：{exc}") from exc
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    raw["promotion_host"] = payload.model_dump(exclude_none=True)
    validated = Config.model_validate(raw)
    backup = CONFIG_PATH.with_suffix(".yml.bak")
    backup.write_text(CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    CONFIG_PATH.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    configure_recurring_jobs(validated)
    audit("web_admin_promotion_host_saved", slot_count=len(payload.slots))
    return {"ok": True, "message": "宣传主持排期已保存并立即生效"}


@router.post("/api/promotion-host/send/{slot_id}")
async def send_promotion_slot_now(
    slot_id: str,
    x_admin_token: str = Header(default=""),
) -> dict[str, Any]:
    require_token(x_admin_token)
    host = load_config().promotion_host
    slot = next((item for item in host.slots if item.id == slot_id), None)
    if slot is None:
        raise HTTPException(404, "该宣传环节不存在")
    await publish_promotion_slot(slot_id, force=True)
    return {"ok": True, "message": f"已发送“{slot.department}”主持消息"}


@router.put("/api/personalization")
async def put_personalization(
    payload: PersonalizationUpdate,
    x_admin_token: str = Header(default=""),
) -> dict[str, Any]:
    require_token(x_admin_token)
    remark_crons = schedule_crons(payload.remark_schedule, payload.remark_cron)
    public_crons = schedule_crons(payload.public_schedule, payload.public_cron)
    for cron in [*remark_crons, *public_crons]:
        validate_cron(cron)
    validate_templates(
        payload.remark_group_template,
        payload.remark_private_template,
        payload.remark_example,
    )
    for push in payload.scheduled_pushes:
        for cron in schedule_crons(push.schedule, push.cron):
            validate_cron(cron)
    for rule in payload.moderation_rules:
        if not rule_pattern(rule):
            raise HTTPException(422, f"规则“{rule.name}”的匹配内容不能为空")
        try:
            re.compile(rule_pattern(rule))
        except re.error as exc:
            raise HTTPException(422, f"规则“{rule.name}”正则无效：{exc}") from exc
    for pattern in payload.allow_patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise HTTPException(422, f"白名单正则无效：{exc}") from exc
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    raw["remark"].update(
        {
            "enabled": payload.remark_enabled,
            "cron": payload.remark_cron,
            "schedule": payload.remark_schedule.model_dump(),
            "remind_cooldown_hours": payload.remark_cooldown_hours,
            "batch_size": payload.remark_batch_size,
            "example": payload.remark_example,
            "group_message_template": payload.remark_group_template,
            "private_message_template": payload.remark_private_template,
        }
    )
    raw["public_account_reminder"].update(
        {
            "enabled": payload.public_enabled,
            "cron": payload.public_cron,
            "schedule": payload.public_schedule.model_dump(),
            "message": payload.public_message,
            "image_paths": payload.public_image_paths,
        }
    )
    raw["moderation"].update(
        {
            "enabled": payload.moderation_enabled,
            "dry_run": payload.moderation_dry_run,
            "blocked_words": sorted({word.strip() for word in payload.blocked_words if word.strip()}),
            "rules": [rule.model_dump() for rule in payload.moderation_rules],
            "allow_patterns": payload.allow_patterns,
        }
    )
    raw["scheduled_pushes"] = [push.model_dump(exclude_none=True) for push in payload.scheduled_pushes]
    validated = Config.model_validate(raw)
    backup = CONFIG_PATH.with_suffix(".yml.bak")
    backup.write_text(CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    CONFIG_PATH.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    configure_recurring_jobs(validated)
    audit("web_admin_personalization_saved", scheduled_push_count=len(validated.scheduled_pushes))
    return {"ok": True, "message": "个性化设置已保存，定时任务已立即重新加载"}


@router.post("/api/upload-image")
async def upload_image(
    image: UploadFile = File(...),
    x_admin_token: str = Header(default=""),
) -> dict[str, str]:
    require_token(x_admin_token)
    extensions = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }
    extension = extensions.get(image.content_type or "")
    if extension is None:
        raise HTTPException(422, "仅支持 JPG、PNG、GIF 或 WebP 图片")
    content = await image.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(422, "图片不能超过5MB")
    upload_dir = CONFIG_PATH.parent / "assets" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{extension}"
    target = upload_dir / filename
    target.write_bytes(content)
    return {
        "path": f"assets/uploads/{filename}",
        "name": image.filename or filename,
    }


@router.get("/api/push-history")
async def push_history(
    limit: int = 100,
    x_admin_token: str = Header(default=""),
) -> list[dict[str, Any]]:
    require_token(x_admin_token)
    limit = max(1, min(limit, 500))
    if not PUSH_HISTORY_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in PUSH_HISTORY_PATH.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(rows))


@router.put("/api/config")
async def put_config(payload: ConfigUpdate, x_admin_token: str = Header(default="")) -> dict[str, Any]:
    require_token(x_admin_token)
    try:
        raw = yaml.safe_load(payload.yaml_text)
        validated = Config.model_validate(raw)
        for pattern in [validated.remark.pattern, *validated.join_review.deny_patterns, *validated.join_review.approve_patterns, *validated.moderation.allow_patterns]:
            re.compile(pattern)
        for rule in validated.moderation.rules:
            re.compile(rule_pattern(rule))
    except Exception as exc:
        raise HTTPException(422, f"配置无效：{exc}") from exc
    temp = CONFIG_PATH.with_suffix(".yml.tmp")
    temp.write_text(payload.yaml_text, encoding="utf-8")
    temp.replace(CONFIG_PATH)
    return {"ok": True, "message": "配置已保存；定时计划变更需重启机器人后生效"}


@router.get("/api/groups")
async def groups(x_admin_token: str = Header(default="")) -> list[dict[str, Any]]:
    require_token(x_admin_token)
    bot = current_bot()
    return await bot.get_group_list(no_cache=True)


@router.get("/api/noncompliant")
async def noncompliant(group_id: int, x_admin_token: str = Header(default="")) -> list[dict[str, Any]]:
    require_token(x_admin_token)
    cfg = load_config()
    require_group(cfg, group_id)
    members = await current_bot().get_group_member_list(group_id=group_id, no_cache=True)
    result = []
    for member in members:
        card = (member.get("card") or "").strip()
        if member.get("role") in {"owner", "admin"} or re.fullmatch(cfg.remark.pattern, card):
            continue
        result.append(
            {
                "user_id": int(member["user_id"]),
                "nickname": member.get("nickname", ""),
                "card": card,
                "role": member.get("role", "member"),
            }
        )
    return result


@router.get("/api/pending")
async def pending(x_admin_token: str = Header(default="")) -> list[dict[str, Any]]:
    require_token(x_admin_token)
    return list(load_pending_removals().values())


@router.get("/api/audit-summary")
async def audit_summary(x_admin_token: str = Header(default="")) -> dict[str, int]:
    require_token(x_admin_token)
    counts: Counter[str] = Counter()
    if AUDIT_PATH.exists():
        for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines()[-1000:]:
            try:
                counts[str(json.loads(line).get("action", "unknown"))] += 1
            except json.JSONDecodeError:
                counts["invalid_json"] += 1
    return dict(counts.most_common(20))


@router.post("/api/action")
async def action(payload: ActionRequest, x_admin_token: str = Header(default="")) -> dict[str, Any]:
    require_token(x_admin_token)
    cfg = load_config()
    require_group(cfg, payload.group_id)
    bot = current_bot()
    if payload.action == "send_message":
        if not payload.message or not payload.message.strip():
            raise HTTPException(422, "消息不能为空")
        await bot.send_group_msg(group_id=payload.group_id, message=payload.message.strip())
        audit("web_admin_message_sent", group_id=payload.group_id, message_chars=len(payload.message.strip()))
        record_push("manual", "success", payload.group_id, payload.message.strip())
        return {"ok": True, "message": "群消息已发送"}
    if payload.action == "remind_now":
        await remind_primary_group_bad_remarks()
        return {"ok": True, "message": "未改群名片检查已执行"}
    if payload.action == "publish_public_account":
        await publish_public_account_reminder()
        return {"ok": True, "message": "公众号提醒已执行"}
    if payload.user_id is None:
        raise HTTPException(422, "该操作需要 user_id")
    protected = {int(bot.self_id), *cfg.admin_commands.authorized_users}
    if payload.user_id in protected:
        raise HTTPException(403, "不能操作机器人或受保护管理员")
    if payload.action == "mute":
        if not payload.duration:
            raise HTTPException(422, "禁言时长必须大于0")
        await bot.set_group_ban(group_id=payload.group_id, user_id=payload.user_id, duration=payload.duration)
        audit("web_admin_mute", group_id=payload.group_id, user_id=payload.user_id, duration=payload.duration)
        return {"ok": True, "message": "禁言已执行"}
    if payload.action == "unmute":
        await bot.set_group_ban(group_id=payload.group_id, user_id=payload.user_id, duration=0)
        audit("web_admin_unmute", group_id=payload.group_id, user_id=payload.user_id)
        return {"ok": True, "message": "解禁已执行"}
    if payload.action == "kick":
        if payload.confirmation != f"移出 {payload.user_id}":
            raise HTTPException(422, f"请输入确认文字：移出 {payload.user_id}")
        info = await bot.get_group_member_info(group_id=payload.group_id, user_id=payload.user_id, no_cache=True)
        if info.get("role") in {"owner", "admin"}:
            raise HTTPException(403, "不能通过后台移出群主或管理员")
        await bot.set_group_kick(group_id=payload.group_id, user_id=payload.user_id, reject_add_request=False)
        audit("web_admin_kick", group_id=payload.group_id, user_id=payload.user_id)
        return {"ok": True, "message": "成员已移出"}
    raise HTTPException(400, "未知操作")


get_driver().server_app.include_router(router)


DASHBOARD_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>QQ 群管理助手</title>
<style>
:root{--bg:#f4f7f6;--panel:#fff;--ink:#17312a;--muted:#6b7d77;--green:#18794e;--green2:#e7f5ed;--red:#c43d3d;--line:#dce7e2;--shadow:0 12px 35px rgba(22,63,49,.08)}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#edf7f1,#f8faf9 45%,#eef5f2);color:var(--ink);font:14px/1.5 system-ui,"Microsoft YaHei",sans-serif}.app{display:grid;grid-template-columns:230px 1fr;min-height:100vh}.side{background:#103e30;color:#fff;padding:28px 18px;position:sticky;top:0;height:100vh}.brand{font-size:20px;font-weight:800;margin:0 8px 28px}.brand small{display:block;font-size:11px;opacity:.65;letter-spacing:.12em;margin-top:4px}.nav button{display:block;width:100%;border:0;background:transparent;color:#dcece5;text-align:left;padding:11px 13px;border-radius:10px;margin:4px 0;cursor:pointer}.nav button.active,.nav button:hover{background:#1c5945;color:#fff}.main{padding:28px;max-width:1380px;width:100%}.top{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px}.title h1{font-size:26px;margin:0}.title p{color:var(--muted);margin:4px 0 0}.status{display:flex;gap:8px;align-items:center}.dot{width:9px;height:9px;border-radius:50%;background:#999}.dot.on{background:#23b26d;box-shadow:0 0 0 5px #dff6e9}.panel{display:none}.panel.active{display:block}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.grid.six{grid-template-columns:repeat(6,1fr)}.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:var(--shadow)}.metric{font-size:30px;font-weight:800;margin:10px 0 0}.label{color:var(--muted);font-size:13px}.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}.btn{border:0;border-radius:10px;padding:10px 15px;background:var(--green);color:#fff;cursor:pointer;font-weight:650}.btn.secondary{background:var(--green2);color:var(--green)}.btn.danger{background:var(--red)}input,textarea,select{width:100%;border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:#fff;color:var(--ink)}textarea{min-height:360px;font:13px/1.55 ui-monospace,Consolas,monospace}.row{display:grid;grid-template-columns:1fr 1fr;gap:14px}.field{margin:12px 0}.field label{display:block;font-weight:650;margin-bottom:5px}.table{width:100%;border-collapse:collapse}.table th,.table td{text-align:left;padding:11px 8px;border-bottom:1px solid var(--line)}.table th{color:var(--muted);font-size:12px}.empty{text-align:center;color:var(--muted);padding:30px}.schedule{padding:12px;background:#f6faf8;border:1px solid var(--line);border-radius:12px}.schedule-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.weekday{display:flex;gap:8px;flex-wrap:wrap}.weekday label,.chip{background:var(--green2);color:var(--green);padding:6px 9px;border-radius:9px}.weekday input{width:auto}.image-list{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}.image-list img{width:72px;height:72px;object-fit:cover;border-radius:10px;border:1px solid var(--line)}.rule-row{display:grid;grid-template-columns:70px 1.2fr 120px 2fr 80px;gap:8px;align-items:center;margin:8px 0}.toast{position:fixed;right:24px;bottom:24px;background:#173f33;color:#fff;padding:12px 16px;border-radius:10px;opacity:0;transform:translateY(8px);transition:.2s}.toast.show{opacity:1;transform:none}.login{position:fixed;inset:0;background:rgba(9,35,27,.72);display:grid;place-items:center;z-index:5}.login .card{width:min(420px,90vw)}.login.hidden{display:none}@media(max-width:1050px){.grid.six{grid-template-columns:repeat(3,1fr)}}@media(max-width:850px){.app{grid-template-columns:1fr}.side{height:auto;position:static;padding:15px}.brand{margin:0 5px 10px}.nav{display:flex;overflow:auto}.nav button{white-space:nowrap}.main{padding:18px}.grid,.grid.six{grid-template-columns:1fr}.row,.schedule-grid,.rule-row{grid-template-columns:1fr}}
</style></head><body>
<div class="login" id="login"><div class="card"><h2>连接管理后台</h2><p class="label">令牌仅保存在当前浏览器会话中。</p><div class="field"><input id="tokenInput" type="password" placeholder="输入管理令牌"></div><button class="btn" onclick="login()">进入后台</button></div></div>
<div class="app"><aside class="side"><div class="brand">群序 · QQ 管理<small>智能群管理助手</small></div><nav class="nav"><button class="active" data-panel="overview">概览</button><button data-panel="members">成员检查</button><button data-panel="pushes">定时提醒</button><button data-panel="promotion">宣传主持</button><button data-panel="joinReview">入群审核</button><button data-panel="rules">内容与名片</button><button data-panel="actions">群操作</button></nav></aside>
<main class="main"><header class="top"><div class="title"><h1 id="pageTitle">运行概览</h1><p>本地、安全、可审计的群管理控制台</p></div><div class="status"><span class="dot" id="dot"></span><span id="online">检查中</span></div></header>
<section class="panel active" id="overview"><div class="grid six"><div class="card"><div class="label">机器人状态</div><div class="metric" id="botStatus">—</div></div><div class="card"><div class="label">管理群数</div><div class="metric" id="groupCount">—</div></div><div class="card"><div class="label">主群成员</div><div class="metric" id="memberCount">—</div></div><div class="card"><div class="label">未合规名片</div><div class="metric" id="badCount">—</div></div><div class="card"><div class="label">待清理名单</div><div class="metric" id="pendingCount">—</div></div><div class="card"><div class="label">今日提醒</div><div class="metric" id="pushCount">—</div></div></div><div class="row" style="margin-top:16px"><div class="card"><h3>接下来要做的事情</h3><div id="nextJobs" class="label"></div></div><div class="card"><h3>今日运行情况</h3><div id="todayStats" class="label"></div></div></div><div class="card" style="margin-top:16px"><h3>快捷操作</h3><div class="toolbar"><button class="btn" onclick="runSimple('remind_now')">立即提醒修改名片</button><button class="btn secondary" onclick="runSimple('publish_public_account')">立即发送默认提醒</button><button class="btn secondary" onclick="refreshAll()">刷新页面数据</button></div></div></section>
<section class="panel" id="members"><div class="card"><div class="top"><div><h3>未改群名片成员</h3><div class="label">自动跳过群主和管理员</div></div><button class="btn secondary" onclick="loadMembers()">刷新</button></div><div style="overflow:auto"><table class="table"><thead><tr><th>昵称</th><th>当前名片</th><th>QQ</th><th>操作</th></tr></thead><tbody id="memberRows"></tbody></table></div></div></section>
<section class="panel" id="pushes"><div class="row"><div class="card"><h3>默认定时提醒</h3><div class="field"><label><input id="publicEnabled" type="checkbox" style="width:auto"> 启用这条提醒</label></div><div id="publicSchedule"></div><div class="field"><label>提醒内容</label><textarea id="publicMessage" style="min-height:150px"></textarea></div></div><div class="card"><h3>新增一条定时提醒</h3><div class="field"><label>提醒名称</label><input id="pushName" placeholder="如：每日群公告"></div><div class="field"><label>发送到哪个群（留空使用主群）</label><input id="pushGroup"></div><div id="pushSchedule"></div><div class="field"><label>提醒内容</label><textarea id="pushMessage" style="min-height:100px"></textarea></div><div class="field"><label>插入图片（最多9张，每张不超过5MB）</label><input id="pushImages" type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple onchange="uploadImages(this)"><div class="image-list" id="pushImageList"></div></div><button class="btn" onclick="addPush()">添加到提醒列表</button></div></div><div class="card" style="margin-top:16px"><div class="top"><div><h3>我的定时提醒</h3><div class="label">每条提醒都可以单独设置时间、群聊、文字和图片</div></div><button class="btn" onclick="savePersonalization()">保存全部提醒</button></div><div style="overflow:auto"><table class="table"><thead><tr><th>启用</th><th>名称</th><th>发送时间</th><th>群聊</th><th>内容/图片</th><th></th></tr></thead><tbody id="pushRows"></tbody></table></div></div><div class="card" style="margin-top:16px"><div class="top"><h3>发送记录</h3><button class="btn secondary" onclick="loadHistory()">刷新</button></div><div style="overflow:auto"><table class="table"><thead><tr><th>时间</th><th>提醒类型</th><th>结果</th><th>群号</th><th>内容</th></tr></thead><tbody id="historyRows"></tbody></table></div></div></section>
<section class="panel" id="joinReview"><div class="card"><div class="top"><div><h3>自动审核入群申请</h3><div class="label">申请内容符合下面的年级、专业和姓名格式时，机器人会自动同意</div></div><label><input id="joinEnabled" type="checkbox" style="width:auto"> 启用自动审核</label></div><div class="row"><div><div class="field"><label>正确格式示例</label><input id="joinExample" placeholder="25 计科 张三"></div><div class="field"><label>允许的年级（每行一个）</label><textarea id="joinYears" style="min-height:150px" placeholder="25&#10;2025&#10;25级"></textarea></div><div class="field"><label>允许的专业简称或全称（每行一个）</label><textarea id="joinMajors" style="min-height:220px" placeholder="计科&#10;网安&#10;计算机科学与技术"></textarea></div></div><div><div class="row"><div class="field"><label>姓名最少字数</label><input id="joinNameMin" type="number" min="1" max="20"></div><div class="field"><label>姓名最多字数</label><input id="joinNameMax" type="number" min="1" max="30"></div></div><div class="field"><label>直接拒绝的词（每行一个）</label><textarea id="joinForbidden" style="min-height:130px" placeholder="广告&#10;刷单&#10;贷款"></textarea></div><div class="field"><label><input id="joinAutoReject" type="checkbox" style="width:auto"> 格式不正确时自动拒绝</label><div class="label">建议先关闭：不符合格式的申请将留给管理员人工判断，减少误拒绝。</div></div><div class="field"><label>拒绝时显示的说明</label><textarea id="joinRejectReason" style="min-height:100px"></textarea></div></div></div><div class="toolbar"><button class="btn" onclick="saveJoinReview()">保存入群审核设置</button><button class="btn secondary" onclick="previewJoinReview()">查看审核说明</button></div><div id="joinPreview" class="label"></div></div></section>
<section class="panel" id="promotion"></section>
<section class="panel" id="rules"><div class="row"><div class="card"><h3>名片提醒</h3><div class="field"><label><input id="remarkEnabled" type="checkbox" style="width:auto"> 启用自动提醒</label></div><div id="remarkSchedule"></div><div class="row"><div class="field"><label>同一成员冷却小时</label><input id="remarkCooldown" type="number"></div><div class="field"><label>每批 @ 人数</label><input id="remarkBatch" type="number"></div></div><div class="field"><label>格式示例</label><textarea id="remarkExample" style="min-height:90px"></textarea></div><div class="field"><label>群提醒模板</label><textarea id="remarkGroupTemplate" style="min-height:180px"></textarea><div class="label">可用变量：{current}、{total}、{mentions}、{example}，必须保留 {mentions}</div></div><div class="field"><label>第三次私信模板</label><textarea id="remarkPrivateTemplate" style="min-height:150px"></textarea></div></div><div class="card"><h3>内容治理</h3><div class="field"><label><input id="moderationEnabled" type="checkbox" style="width:auto"> 启用内容治理</label>　<label><input id="moderationDryRun" type="checkbox" style="width:auto"> 仅观察，不自动撤回</label></div><div class="field"><label>屏蔽词（每行一个，适合简单关键词）</label><textarea id="blockedWords" style="min-height:130px"></textarea></div><div class="field"><div class="top"><div><label>个性化治理规则</label><div class="label">直接选择“包含关键词”或“正则表达式”，无需填写 JSON</div></div><button class="btn secondary" onclick="addRule()">新增规则</button></div><div id="ruleRows"></div></div><div class="field"><label>允许内容（正则，每行一个）</label><textarea id="allowPatterns" style="min-height:100px"></textarea></div><button class="btn" onclick="savePersonalization()">校验并保存规则</button></div></div></section>
<section class="panel" id="actions"><div class="row"><div class="card"><h3>发送群消息</h3><div class="field"><label>群号</label><input id="msgGroup"></div><div class="field"><label>消息内容</label><textarea id="message" style="min-height:130px"></textarea></div><button class="btn" onclick="sendMessage()">发送</button></div><div class="card"><h3>成员操作</h3><div class="field"><label>群号</label><input id="opGroup"></div><div class="field"><label>成员 QQ</label><input id="userId"></div><div class="field"><label>禁言秒数</label><input id="duration" type="number" value="600"></div><div class="toolbar"><button class="btn" onclick="memberAction('mute')">禁言</button><button class="btn secondary" onclick="memberAction('unmute')">解禁</button><button class="btn danger" onclick="memberAction('kick')">移出群聊</button></div></div></div></section>
<section class="panel" id="config"><div class="card"><div class="top"><div><h3>YAML 配置</h3><div class="label">保存前自动校验；定时计划修改后需重启</div></div><div class="toolbar"><button class="btn secondary" onclick="loadConfig()">重新载入</button><button class="btn" onclick="saveConfig()">校验并保存</button></div></div><textarea id="yaml"></textarea></div></section>
</main></div><div class="toast" id="toast"></div>
<script>
let token=sessionStorage.getItem('qqAdminToken')||'', primaryGroup='', personal=null, pendingImages=[], publicImagePaths=[], joinData=null, promotionData=null;
const headers=()=>({'Content-Type':'application/json','X-Admin-Token':token});
async function api(path,opts={}){const r=await fetch('/qq-admin/api/'+path,{...opts,headers:{...headers(),...(opts.headers||{})}});const data=await r.json().catch(()=>({detail:r.statusText}));if(!r.ok)throw new Error(data.detail||'请求失败');return data}
function toast(msg,bad=false){const e=document.getElementById('toast');e.textContent=msg;e.style.background=bad?'#9f2f2f':'#173f33';e.classList.add('show');setTimeout(()=>e.classList.remove('show'),2600)}
async function login(){token=document.getElementById('tokenInput').value.trim();sessionStorage.setItem('qqAdminToken',token);try{await refreshAll();document.getElementById('login').classList.add('hidden')}catch(e){toast(e.message,true)}}
async function refreshAll(){const s=await api('status');primaryGroup=s.primary_group;msgGroup.value=primaryGroup;opGroup.value=primaryGroup;botStatus.textContent=s.online?'在线':'离线';online.textContent=s.online?'机器人在线':'机器人离线';dot.className='dot '+(s.online?'on':'');const [m,p,o]=await Promise.all([api('noncompliant?group_id='+primaryGroup),api('pending'),api('overview')]);badCount.textContent=o.noncompliant_count;pendingCount.textContent=o.pending_count;groupCount.textContent=o.group_count;memberCount.textContent=o.member_count;pushCount.textContent=o.pushes_today;const jobName=id=>id.startsWith('remark_reminder')?'检查群名片':id.startsWith('public_account_reminder')?'发送默认提醒':id.startsWith('promotion_host_')?'宣传活动主持':'发送自定义提醒';nextJobs.innerHTML=o.next_jobs.length?o.next_jobs.map(x=>`<div>⏱ ${jobName(x.id)}<br><small>${esc((x.next_run||'等待安排').replace('T',' ').slice(0,19))}</small></div>`).join('<hr>'):'暂时没有自动任务';todayStats.innerHTML=`提醒发送失败：${o.push_failures_today}<br>不当内容处理：${o.moderation_hits_today}<br>自动同意入群：${o.join_approved_today}<br>等待人工审核：${o.join_pending_today}`;renderMembers(m)}
function renderMembers(items){const b=document.getElementById('memberRows');b.innerHTML=items.length?items.map(x=>`<tr><td>${esc(x.nickname)}</td><td>${esc(x.card||'未设置')}</td><td>${x.user_id}</td><td><button class="btn danger" onclick="kick(${x.user_id})">移出</button></td></tr>`).join(''):'<tr><td colspan="4" class="empty">当前没有未合规成员</td></tr>'}
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function loadMembers(){try{renderMembers(await api('noncompliant?group_id='+primaryGroup))}catch(e){toast(e.message,true)}}
async function runSimple(action){try{const d=await api('action',{method:'POST',body:JSON.stringify({action,group_id:Number(primaryGroup)})});toast(d.message);refreshAll()}catch(e){toast(e.message,true)}}
async function sendMessage(){try{const d=await api('action',{method:'POST',body:JSON.stringify({action:'send_message',group_id:Number(msgGroup.value),message:message.value})});toast(d.message)}catch(e){toast(e.message,true)}}
async function memberAction(action){const uid=Number(userId.value),body={action,group_id:Number(opGroup.value),user_id:uid,duration:Number(duration.value)};if(action==='kick'){const c=prompt('高风险操作。请输入：移出 '+uid);if(c===null)return;body.confirmation=c}try{const d=await api('action',{method:'POST',body:JSON.stringify(body)});toast(d.message);refreshAll()}catch(e){toast(e.message,true)}}
async function kick(uid){userId.value=uid;opGroup.value=primaryGroup;await memberAction('kick')}
async function loadConfig(){try{document.getElementById('yaml').value=(await api('config')).yaml_text}catch(e){toast(e.message,true)}}
async function saveConfig(){try{const d=await api('config',{method:'PUT',body:JSON.stringify({yaml_text:document.getElementById('yaml').value})});toast(d.message)}catch(e){toast(e.message,true)}}
function scheduleEditor(id,s={mode:'daily_interval',start_hour:9,end_hour:21,interval_hours:2,minute:0,times:['09:00'],weekdays:[1,2,3,4,5]}){const days=['周一','周二','周三','周四','周五','周六','周日'];return `<div class="schedule"><div class="field"><label>执行方式</label><select id="${id}Mode" onchange="toggleSchedule('${id}')"><option value="daily_interval" ${s.mode==='daily_interval'?'selected':''}>每天按间隔执行</option><option value="daily_times" ${s.mode==='daily_times'?'selected':''}>每天指定多个时间</option><option value="weekly" ${s.mode==='weekly'?'selected':''}>每周指定日期和时间</option></select></div><div id="${id}Interval" class="schedule-grid"><div><label>开始小时</label><input id="${id}Start" type="number" min="0" max="23" value="${s.start_hour??9}"></div><div><label>结束小时</label><input id="${id}End" type="number" min="0" max="23" value="${s.end_hour??21}"></div><div><label>每隔几小时</label><input id="${id}Every" type="number" min="1" max="24" value="${s.interval_hours??2}"></div><div><label>第几分钟</label><input id="${id}Minute" type="number" min="0" max="59" value="${s.minute??0}"></div></div><div id="${id}Times" class="field"><label>执行时间（逗号分隔）</label><input id="${id}TimeValues" value="${esc((s.times||['09:00']).join(', '))}" placeholder="09:00, 13:30, 18:00"></div><div id="${id}Days" class="field"><label>执行星期</label><div class="weekday">${days.map((d,i)=>`<label><input type="checkbox" data-day="${i+1}" ${(s.weekdays||[]).includes(i+1)?'checked':''}> ${d}</label>`).join('')}</div></div></div>`}
function toggleSchedule(id){const mode=document.getElementById(id+'Mode').value;document.getElementById(id+'Interval').style.display=mode==='daily_interval'?'grid':'none';document.getElementById(id+'Times').style.display=mode==='daily_interval'?'none':'block';document.getElementById(id+'Days').style.display=mode==='weekly'?'block':'none'}
function readSchedule(id){const mode=document.getElementById(id+'Mode').value,times=document.getElementById(id+'TimeValues').value.split(',').map(x=>x.trim()).filter(Boolean),weekdays=[...document.querySelectorAll(`#${id}Days input:checked`)].map(x=>Number(x.dataset.day));return {mode,start_hour:Number(document.getElementById(id+'Start').value),end_hour:Number(document.getElementById(id+'End').value),interval_hours:Number(document.getElementById(id+'Every').value),minute:Number(document.getElementById(id+'Minute').value),times,weekdays}}
function scheduleText(s){if(!s)return '沿用原计划';if(s.mode==='daily_interval')return `每天 ${String(s.start_hour).padStart(2,'0')}:${String(s.minute).padStart(2,'0')} 至 ${String(s.end_hour).padStart(2,'0')}:${String(s.minute).padStart(2,'0')}，每 ${s.interval_hours} 小时`;if(s.mode==='weekly')return `每周 ${s.weekdays.join('/')}：${s.times.join('、')}`;return `每天 ${s.times.join('、')}`}
const mediaUrl=p=>'/qq-admin/media/'+p.replace(/^assets\//,'').split('/').map(encodeURIComponent).join('/');
function ensurePublicImageEditor(){if(document.getElementById('publicImageList'))return;publicMessage.parentElement.insertAdjacentHTML('afterend','<div class="field"><label>提醒中的图片</label><div class="image-list" id="publicImageList"></div><input id="publicImages" type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple onchange="uploadPublicImages(this)"><div class="label">图片会和上面的文字一起发送，最多9张。</div></div>')}
function renderPublicImages(){ensurePublicImageEditor();publicImageList.innerHTML=publicImagePaths.map((p,i)=>`<span><img src="${mediaUrl(p)}" alt="提醒图片"><button class="btn danger" onclick="publicImagePaths.splice(${i},1);renderPublicImages()">删除</button></span>`).join('')||'<div class="label">这条提醒还没有图片</div>'}
async function uploadPublicImages(input){if(publicImagePaths.length+input.files.length>9){toast('每条提醒最多9张图片',true);return}for(const file of input.files){const form=new FormData();form.append('image',file);try{const r=await fetch('/qq-admin/api/upload-image',{method:'POST',headers:{'X-Admin-Token':token},body:form}),d=await r.json();if(!r.ok)throw new Error(d.detail);publicImagePaths.push(d.path)}catch(e){toast(e.message,true)}}renderPublicImages();input.value=''}
async function loadPersonalization(){try{personal=await api('personalization');publicImagePaths=[...(personal.public_image_paths||[])];remarkEnabled.checked=personal.remark_enabled;remarkCooldown.value=personal.remark_cooldown_hours;remarkBatch.value=personal.remark_batch_size;remarkExample.value=personal.remark_example;remarkGroupTemplate.value=personal.remark_group_template;remarkPrivateTemplate.value=personal.remark_private_template;publicEnabled.checked=personal.public_enabled;publicMessage.value=personal.public_message;moderationEnabled.checked=personal.moderation_enabled;moderationDryRun.checked=personal.moderation_dry_run;blockedWords.value=personal.blocked_words.join('\n');allowPatterns.value=personal.allow_patterns.join('\n');remarkSchedule.innerHTML=scheduleEditor('remark',personal.remark_schedule);publicSchedule.innerHTML=scheduleEditor('public',personal.public_schedule);pushSchedule.innerHTML=scheduleEditor('push');['remark','public','push'].forEach(toggleSchedule);renderPublicImages();renderRules();renderPushes()}catch(e){toast(e.message,true)}}
function renderPushes(){const items=personal?.scheduled_pushes||[];pushRows.innerHTML=items.length?items.map((p,i)=>`<tr><td><input type="checkbox" style="width:auto" ${p.enabled?'checked':''} onchange="personal.scheduled_pushes[${i}].enabled=this.checked"></td><td>${esc(p.name)}</td><td>${esc(scheduleText(p.schedule))}</td><td>${p.group_id||'主群'}</td><td title="${esc(p.message)}">${esc(p.message.slice(0,34))}<div class="image-list">${(p.image_paths||[]).map(path=>`<img src="${mediaUrl(path)}" alt="提醒图片">`).join('')}</div></td><td><button class="btn danger" onclick="removePush(${i})">删除</button></td></tr>`).join(''):'<tr><td colspan="6" class="empty">尚未添加自定义提醒</td></tr>'}
async function uploadImages(input){if(pendingImages.length+input.files.length>9){toast('每条推送最多9张图片',true);return}for(const file of input.files){const form=new FormData();form.append('image',file);try{const r=await fetch('/qq-admin/api/upload-image',{method:'POST',headers:{'X-Admin-Token':token},body:form}),d=await r.json();if(!r.ok)throw new Error(d.detail);pendingImages.push(d.path)}catch(e){toast(e.message,true)}}renderPendingImages();input.value=''}
function renderPendingImages(){pushImageList.innerHTML=pendingImages.map((p,i)=>`<span><img src="${mediaUrl(p)}" alt="待发送图片"><button class="btn danger" onclick="pendingImages.splice(${i},1);renderPendingImages()">删除</button></span>`).join('')}
function addPush(){if(!personal)return;const id='reminder_'+Date.now(),name=pushName.value.trim(),msg=pushMessage.value.trim();if(!name||(!msg&&!pendingImages.length)){toast('请填写提醒名称，并提供文字或图片',true);return}personal.scheduled_pushes.push({id,name,enabled:true,cron:'0 0 * * *',schedule:readSchedule('push'),message:msg,group_id:pushGroup.value?Number(pushGroup.value):null,image_paths:[...pendingImages]});renderPushes();pushName.value='';pushMessage.value='';pendingImages=[];renderPendingImages()}
function removePush(i){personal.scheduled_pushes.splice(i,1);renderPushes()}
function renderRules(){ruleRows.innerHTML=(personal.moderation_rules||[]).map((r,i)=>`<div class="rule-row"><label><input type="checkbox" style="width:auto" ${r.enabled!==false?'checked':''} onchange="personal.moderation_rules[${i}].enabled=this.checked"> 启用</label><input value="${esc(r.name)}" oninput="personal.moderation_rules[${i}].name=this.value" placeholder="规则名称"><select onchange="personal.moderation_rules[${i}].match_type=this.value"><option value="contains" ${r.match_type==='contains'?'selected':''}>包含关键词</option><option value="regex" ${r.match_type!=='contains'?'selected':''}>正则表达式</option></select><input value="${esc(r.value||r.pattern||'')}" oninput="personal.moderation_rules[${i}].value=this.value" placeholder="匹配内容"><button class="btn danger" onclick="personal.moderation_rules.splice(${i},1);renderRules()">删除</button></div>`).join('')||'<div class="empty">暂无个性化规则</div>'}
function addRule(){personal.moderation_rules.push({name:'新规则',enabled:true,match_type:'contains',value:'',pattern:''});renderRules()}
async function savePersonalization(){try{const body={remark_enabled:remarkEnabled.checked,remark_cron:'0 0 * * *',remark_schedule:readSchedule('remark'),remark_cooldown_hours:Number(remarkCooldown.value),remark_batch_size:Number(remarkBatch.value),remark_example:remarkExample.value,remark_group_template:remarkGroupTemplate.value,remark_private_template:remarkPrivateTemplate.value,public_enabled:publicEnabled.checked,public_cron:'0 0 * * *',public_schedule:readSchedule('public'),public_message:publicMessage.value,public_image_paths:publicImagePaths,moderation_enabled:moderationEnabled.checked,moderation_dry_run:moderationDryRun.checked,blocked_words:blockedWords.value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean),moderation_rules:personal.moderation_rules,allow_patterns:allowPatterns.value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean),scheduled_pushes:personal.scheduled_pushes};const d=await api('personalization',{method:'PUT',body:JSON.stringify(body)});toast(d.message);await loadPersonalization();refreshAll()}catch(e){toast(e.message,true)}}
async function loadHistory(){try{const rows=await api('push-history?limit=100'),typeName=x=>x==='public_account'?'默认提醒':x==='remark'?'名片提醒':'自定义提醒',statusName=x=>x==='success'?'发送成功':x==='failed'?'发送失败':x;historyRows.innerHTML=rows.length?rows.map(x=>`<tr><td>${esc(x.time.replace('T',' ').slice(0,19))}</td><td>${typeName(x.type)}</td><td>${statusName(x.status)}</td><td>${x.group_id}</td><td title="${esc(x.content)}">${esc(x.content.slice(0,60))}</td></tr>`).join(''):'<tr><td colspan="5" class="empty">暂无发送记录</td></tr>'}catch(e){toast(e.message,true)}}
function ensureJoinEditor(){joinReview.innerHTML=`<div class="card"><div class="top"><div><h3>自动审核入群申请</h3><div class="label">按顺序添加需要申请人填写的内容，适用于学校、社团、公司、兴趣群等不同场景。</div></div><label><input id="joinEnabled" type="checkbox" style="width:auto"> 启用自动审核</label></div><div class="top"><div><h3>申请格式</h3><div class="label">每一项就是申请人需要填写的一段内容。</div></div><button class="btn secondary" onclick="addJoinField()">添加一项</button></div><div id="joinFieldRows"></div><div class="row"><div class="field"><label>直接拒绝的词（每行一个）</label><textarea id="joinForbidden" style="min-height:130px"></textarea></div><div><div class="field"><label><input id="joinAutoReject" type="checkbox" style="width:auto"> 格式不正确时自动拒绝</label><div class="label">关闭时，不符合格式的申请会留给管理员判断。</div></div><div class="field"><label>拒绝时显示的说明</label><textarea id="joinRejectReason" style="min-height:100px"></textarea></div></div></div><div class="toolbar"><button class="btn" onclick="saveJoinReview()">保存入群审核设置</button><button class="btn secondary" onclick="previewJoinReview()">更新格式预览</button></div><div id="joinPreview" class="schedule"></div></div>`}
function renderJoinFields(){joinFieldRows.innerHTML=joinData.fields.map((f,i)=>`<div class="schedule" style="margin:12px 0"><div class="top"><b>第 ${i+1} 项</b><div><button class="btn secondary" onclick="moveJoinField(${i},-1)">上移</button> <button class="btn secondary" onclick="moveJoinField(${i},1)">下移</button> <button class="btn danger" onclick="joinData.fields.splice(${i},1);renderJoinFields();previewJoinReview()">删除</button></div></div><div class="schedule-grid"><div><label>项目名称</label><input value="${esc(f.name)}" oninput="joinData.fields[${i}].name=this.value;previewJoinReview()"></div><div><label>检查方式</label><select onchange="joinData.fields[${i}].kind=this.value;renderJoinFields();previewJoinReview()"><option value="text" ${f.kind==='text'?'selected':''}>任意文字</option><option value="options" ${f.kind==='options'?'selected':''}>只能从允许值中选择</option><option value="number" ${f.kind==='number'?'selected':''}>只能填写数字</option><option value="chinese" ${f.kind==='chinese'?'selected':''}>只能填写中文</option></select></div><div><label>合格示例</label><input value="${esc(f.example)}" oninput="joinData.fields[${i}].example=this.value;previewJoinReview()"></div><div><label>允许值（逗号分隔）</label><input value="${esc((f.options||[]).join(', '))}" ${f.kind==='options'?'':'disabled'} oninput="joinData.fields[${i}].options=this.value.split(',').map(x=>x.trim()).filter(Boolean);previewJoinReview()"></div><div><label>最少字数</label><input type="number" min="1" max="50" value="${f.min_length}" oninput="joinData.fields[${i}].min_length=Number(this.value)"></div><div><label>最多字数</label><input type="number" min="1" max="100" value="${f.max_length}" oninput="joinData.fields[${i}].max_length=Number(this.value)"></div></div></div>`).join('')||'<div class="empty">请至少添加一项申请内容</div>'}
function addJoinField(){joinData.fields.push({name:'新项目',kind:'text',options:[],example:'示例内容',min_length:1,max_length:30});renderJoinFields();previewJoinReview()}
function moveJoinField(i,d){const n=i+d;if(n<0||n>=joinData.fields.length)return;[joinData.fields[i],joinData.fields[n]]=[joinData.fields[n],joinData.fields[i]];renderJoinFields();previewJoinReview()}
async function loadJoinReview(){try{joinData=await api('join-review');ensureJoinEditor();joinEnabled.checked=joinData.enabled;joinForbidden.value=joinData.forbidden_words.join('\n');joinAutoReject.checked=joinData.auto_reject_invalid;joinRejectReason.value=joinData.reject_reason;renderJoinFields();previewJoinReview()}catch(e){toast(e.message,true)}}
function previewJoinReview(){if(!joinData)return;const example=joinData.fields.map(f=>f.example||f.name).join(' ＋ '),details=joinData.fields.map(f=>`${f.name}：${f.kind==='options'?'可填写 '+(f.options||[]).join('、'):f.kind==='number'?'填写数字':f.kind==='chinese'?'填写中文':'填写文字'}`).join('；');joinPreview.innerHTML=`<b>符合条件的格式：</b>${esc(example||'请先添加项目')}<br><span class="label">${esc(details)}</span>`}
async function saveJoinReview(){try{const lines=id=>document.getElementById(id).value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean),body={enabled:joinEnabled.checked,fields:joinData.fields,forbidden_words:lines('joinForbidden'),auto_reject_invalid:joinAutoReject.checked,reject_reason:joinRejectReason.value};const d=await api('join-review',{method:'PUT',body:JSON.stringify(body)});toast(d.message);loadJoinReview()}catch(e){toast(e.message,true)}}
function ensurePromotionEditor(){promotion.innerHTML=`<div class="card"><div class="top"><div><h3>社团宣传主持</h3><div class="label">机器人会在每个环节的开始时间自动介绍当前宣传部门。</div></div><label><input id="promotionEnabled" type="checkbox" style="width:auto"> 启用本次活动</label></div><div class="row"><div class="field"><label>活动日期</label><input id="promotionDate" type="date"></div><div class="field"><label>发送群号（留空使用主群）</label><input id="promotionGroup" inputmode="numeric" placeholder="使用主群"></div></div><div class="field"><label>统一主持词模板</label><textarea id="promotionTemplate" style="min-height:120px"></textarea><div class="label">可用内容：{department} 社团名称、{start_time} 开始时间、{end_time} 结束时间、{content} 本环节内容。</div></div><div class="schedule"><h3>批量导入流程</h3><div class="label">粘贴后自动按换行、空格或制表符识别。支持：14:00-14:10 团委办公室；14:00 14:10 团委办公室；14:00 团委办公室（默认10分钟）。多条内容也可以放在同一行。</div><textarea id="promotionImportText" style="min-height:150px" placeholder="14:00-14:10 团委办公室&#10;14:10-14:20 网计学院青年志愿者分队"></textarea><div class="toolbar"><button class="btn secondary" onclick="previewPromotionImport()">识别并预览</button><button class="btn" onclick="applyPromotionImport(false)">替换当前流程</button><button class="btn secondary" onclick="applyPromotionImport(true)">追加到流程</button></div><div id="promotionImportPreview" class="label">尚未识别文本</div></div><div class="top"><div><h3>活动流程</h3><div class="label">时间、名称和主持内容均可修改，也可以用上方文本批量生成。</div></div><button class="btn secondary" onclick="addPromotionSlot()">新增环节</button></div><div style="overflow:auto"><table class="table"><thead><tr><th>开始</th><th>结束</th><th>宣传部门/社团</th><th>主持内容</th><th>操作</th></tr></thead><tbody id="promotionRows"></tbody></table></div><div class="toolbar"><button class="btn" onclick="savePromotion()">保存排期设置</button><button class="btn secondary" onclick="previewPromotion()">预览主持词</button></div><div id="promotionPreview" class="schedule"></div></div>`}
function renderPromotionRows(){promotionRows.innerHTML=promotionData.slots.length?promotionData.slots.map((s,i)=>`<tr><td><input type="time" value="${s.start_time}" onchange="promotionData.slots[${i}].start_time=this.value"></td><td><input type="time" value="${s.end_time}" onchange="promotionData.slots[${i}].end_time=this.value"></td><td><input value="${esc(s.department)}" oninput="promotionData.slots[${i}].department=this.value"></td><td><textarea style="min-height:75px" oninput="promotionData.slots[${i}].content=this.value">${esc(s.content||'')}</textarea></td><td><button class="btn secondary" onclick="sendPromotionNow(${i})">立即发送</button> <button class="btn danger" onclick="promotionData.slots.splice(${i},1);renderPromotionRows()">删除</button></td></tr>`).join(''):'<tr><td colspan="5" class="empty">还没有宣传环节</td></tr>'}
function addMinutes(value,minutes){const [h,m]=(value||'14:00').split(':').map(Number),d=new Date(2000,0,1,h,m+minutes);return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`}
function addPromotionSlot(){const last=promotionData.slots.at(-1),start=last?.end_time||'14:00';promotionData.slots.push({id:'slot_'+Date.now(),start_time:start,end_time:addMinutes(start,10),department:'新宣传部门',content:'请负责人进行宣传介绍。'});renderPromotionRows()}
function parsePromotionImport(text){const normalized=(text||'').replaceAll('：',':').replace(/\r/g,' ').replace(/(?<!\d)(?=(?:[01]?\d|2[0-3]):[0-5]\d\s*(?:[-—–~至]\s*(?:[01]?\d|2[0-3]):[0-5]\d|\s+(?:[01]?\d|2[0-3]):[0-5]\d))/g,'\n');const lines=normalized.split(/\n+/).map(x=>x.trim()).filter(Boolean),slots=[],errors=[];lines.forEach((line,index)=>{let m=line.match(/^(\d{1,2}:\d{2})\s*(?:[-—–~至]\s*|\s+)(\d{1,2}:\d{2})\s+(.+)$/),start,end,department;if(m){[,start,end,department]=m}else{m=line.match(/^(\d{1,2}:\d{2})\s+(.+)$/);if(m){start=m[1];end=addMinutes(start,10);department=m[2]}}if(!m||!department){errors.push(`第${index+1}段无法识别：${line}`);return}start=start.split(':').map(v=>String(Number(v)).padStart(2,'0')).join(':');end=end.split(':').map(v=>String(Number(v)).padStart(2,'0')).join(':');if(!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(start)||!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(end)){errors.push(`第${index+1}段时间无效：${line}`);return}department=department.trim().replace(/^[|·•，,、]+|[|·•，,、]+$/g,'').trim();if(!department){errors.push(`第${index+1}段缺少部门名称`);return}slots.push({id:`slot_${start.replace(':','')}_${index}_${Date.now()}`,start_time:start,end_time:end,department,content:`请${department}负责人进行宣传介绍。`})});return{slots,errors}}
function previewPromotionImport(){const result=parsePromotionImport(promotionImportText.value);promotionImportPreview.innerHTML=result.slots.length?`已识别 <b>${result.slots.length}</b> 个环节：<br>${result.slots.map(x=>`${esc(x.start_time)}—${esc(x.end_time)}　${esc(x.department)}`).join('<br>')}${result.errors.length?'<br><span style="color:#c33">'+result.errors.map(esc).join('<br>')+'</span>':''}`:`<span style="color:#c33">没有识别到环节。${result.errors.map(esc).join('<br>')}</span>`;return result}
function applyPromotionImport(append){const result=previewPromotionImport();if(!result.slots.length)return toast('没有可导入的环节，请检查文本格式',true);if(!append&&promotionData.slots.length&&!confirm(`将用识别出的 ${result.slots.length} 个环节替换当前流程，是否继续？`))return;promotionData.slots=append?[...promotionData.slots,...result.slots]:result.slots;promotionData.slots.sort((a,b)=>a.start_time.localeCompare(b.start_time));renderPromotionRows();previewPromotion();toast(`已${append?'追加':'导入'} ${result.slots.length} 个环节，请点击“保存排期设置”使其生效`)}
async function loadPromotion(){try{promotionData=await api('promotion-host');ensurePromotionEditor();promotionEnabled.checked=promotionData.enabled;promotionDate.value=promotionData.event_date||'';promotionGroup.value=promotionData.group_id||'';promotionTemplate.value=promotionData.message_template;renderPromotionRows();previewPromotion()}catch(e){toast(e.message,true)}}
function previewPromotion(){if(!promotionData||!promotionData.slots.length){promotionPreview.textContent='请先添加一个宣传环节';return}const s=promotionData.slots[0],tpl=promotionTemplate.value;promotionPreview.innerHTML='<b>首个环节预览：</b><br>'+esc(tpl.replaceAll('{department}',s.department).replaceAll('{start_time}',s.start_time).replaceAll('{end_time}',s.end_time).replaceAll('{content}',s.content||'' )).replace(/\n/g,'<br>')}
async function savePromotion(){try{promotionData.enabled=promotionEnabled.checked;promotionData.event_date=promotionDate.value||null;promotionData.group_id=promotionGroup.value?Number(promotionGroup.value):null;promotionData.message_template=promotionTemplate.value;const d=await api('promotion-host',{method:'PUT',body:JSON.stringify(promotionData)});toast(d.message);await loadPromotion();refreshAll()}catch(e){toast(e.message,true)}}
async function sendPromotionNow(i){const s=promotionData.slots[i];if(!confirm(`确定立即向群里发送“${s.department}”的主持消息吗？`))return;try{await savePromotion();const d=await api('promotion-host/send/'+encodeURIComponent(s.id),{method:'POST'});toast(d.message)}catch(e){toast(e.message,true)}}
document.querySelectorAll('.nav button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.nav button,.panel').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.getElementById(b.dataset.panel).classList.add('active');document.getElementById('pageTitle').textContent=b.textContent;if(b.dataset.panel==='members')loadMembers();if(['pushes','rules'].includes(b.dataset.panel))loadPersonalization();if(b.dataset.panel==='pushes')loadHistory();if(b.dataset.panel==='joinReview')loadJoinReview();if(b.dataset.panel==='promotion')loadPromotion()});
if(token){refreshAll().then(()=>document.getElementById('login').classList.add('hidden')).catch(()=>{})}
</script></body></html>'''
