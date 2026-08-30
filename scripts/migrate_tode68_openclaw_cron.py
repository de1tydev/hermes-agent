#!/usr/bin/env python3
"""Migrate TODE68 OpenClaw cron definitions into isolated Hermes Profiles."""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

import yaml
from croniter import croniter


SCHEMA = "tode68-openclaw-cron-migration/v1"
TIMEZONE = "Asia/Shanghai"
SOURCE_JOBS = Path("cron/jobs.json.migrated")
SOURCE_STATE = Path("cron/jobs-state.json.migrated")
SYSTEM_REVIEW_REASONS = {
    "openclaw-git-backup": "OpenClaw-specific Git backup command has no Hermes target repository",
    "openclaw-backup-status": "OpenClaw-specific backup status command has no Hermes equivalent state",
    "Memory Dreaming Promotion": "OpenClaw Memory Dreaming is incompatible with the built-in Hermes Memory boundary",
    "auto-fill-agent-names": "OpenClaw Agent-name maintenance is obsolete under deterministic Hermes Profiles",
}
LOCAL_DELIVERY_NAMES = {"water-reminder-ding-jiayu"}
HOME_DELIVERY_NOTE = (
    "\n\n【Hermes 迁移规则，优先执行】只返回最终内容，不要调用 message、"
    "send_message 或 feishu_im_user_message。Hermes 调度器会把最终内容发送到当前 "
    "Profile 自己的 Feishu Home Channel。"
)
LOCAL_DELIVERY_NOTE = (
    "\n\n【Hermes 迁移规则，优先执行】按上面的明确收件人调用 send_message；"
    "完成后回复 NO_REPLY，避免调度器重复投递。"
)
IDENTITY_RE = re.compile(r"(?:user:|chat:|feishu:)*((?:ou|oc)_[A-Za-z0-9]+)")


class CronMigrationError(RuntimeError):
    pass


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise CronMigrationError(f"required regular JSON missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CronMigrationError(f"JSON root is not an object: {path}")
    return value


def atomic_write(path: Path, payload: bytes, *, uid: int, gid: int, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw)
    try:
        os.fchmod(fd, mode)
        if os.geteuid() == 0:
            os.fchown(fd, uid, gid)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def cron_store_lock(cron_dir: Path, *, uid: int, gid: int) -> Iterator[None]:
    cron_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cron_dir / ".jobs.lock"
    with lock_path.open("a+") as handle:
        os.chmod(lock_path, 0o600)
        if os.geteuid() == 0:
            os.chown(lock_path, uid, gid)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def identity_digest(kind: str, identity: str) -> str:
    return hashlib.sha256(f"feishu\0{kind}\0{identity}".encode()).hexdigest()


def load_identity_profiles(target: Path) -> tuple[dict[tuple[str, str], str], dict[str, list[str]]]:
    manifest = read_json(target / "migration/migration-manifest.json")
    identity_profiles: dict[tuple[str, str], str] = {}
    agent_profiles: dict[str, list[str]] = defaultdict(list)
    for row in manifest.get("profiles") or []:
        if not isinstance(row, dict):
            continue
        profile = row.get("profile")
        agent = row.get("source_agent")
        kind = row.get("kind")
        digest = str(row.get("identity_digest") or "").removeprefix("sha256:")
        if isinstance(profile, str) and isinstance(agent, str):
            agent_profiles[agent].append(profile)
        if kind in {"dm", "group"} and digest and isinstance(profile, str):
            identity_profiles[(kind, digest)] = profile
    return identity_profiles, dict(agent_profiles)


def identities_in(value: Any) -> list[tuple[str, str]]:
    if not isinstance(value, str):
        return []
    result = []
    for identity in IDENTITY_RE.findall(value):
        item = ("dm" if identity.startswith("ou_") else "group", identity)
        if item not in result:
            result.append(item)
    return result


def resolve_profile(
    job: dict[str, Any],
    identity_profiles: dict[tuple[str, str], str],
    agent_profiles: dict[str, list[str]],
) -> tuple[str, str]:
    values = [
        ("delivery", (job.get("delivery") or {}).get("to")),
        ("session", job.get("sessionKey")),
        ("prompt", (job.get("payload") or {}).get("message")),
    ]
    for basis, value in values:
        mapped = []
        for kind, identity in identities_in(value):
            profile = identity_profiles.get((kind, identity_digest(kind, identity)))
            if profile and profile not in mapped:
                mapped.append(profile)
        if len(mapped) == 1:
            return mapped[0], basis
        if len(mapped) > 1:
            if job.get("name") in LOCAL_DELIVERY_NAMES:
                return "main", "multi-recipient-tool"
            raise CronMigrationError(f"ambiguous cron recipients: {job.get('name')}")
    agent = job.get("agentId")
    profiles = agent_profiles.get(agent, []) if isinstance(agent, str) else []
    if len(profiles) == 1:
        return profiles[0], "agent"
    raise CronMigrationError(f"could not resolve cron Profile: {job.get('name')}")


def adapt_prompt(job: dict[str, Any], profile: str, deliver: str) -> str:
    prompt = str((job.get("payload") or {}).get("message") or "").strip()
    if not prompt:
        raise CronMigrationError(f"empty cron prompt: {job.get('name')}")
    prompt = prompt.replace("minimax__web_search", "zhipu-search 或 baidu-search Skill")
    prompt = prompt.replace("feishu_im_user_message", "send_message")
    prompt = prompt.replace("2026-05-16", "当天日期")
    prompt = prompt.replace("2026年5月16日", "当天日期")
    prompt = prompt.replace("2026年5月", "当月")
    if job.get("name") == "daily-cron-report-9am":
        prompt = f"""请生成 Hermes 多 Profile 定时任务日报：

1. 读取 /opt/data/profiles/*/cron/jobs.json，汇总所有 Profile 的任务定义。
2. 读取 /opt/data/profiles/{profile}/workspace/tools/feishu-id-cache.json 获取可用的姓名映射。
3. 以 Profile 的 platforms.feishu.home_channel.name 作为接收者名称；没有名称时显示 Profile 名称，不输出原始用户或群聊 ID。
4. 同时列出启用和停用任务，并明确标注状态；不要读取或汇报执行历史、日志和输出正文。
5. 输出格式为“定时任务日报（日期）”，按接收者分组，逐项给出任务名称、状态、频率/时间和一句话内容摘要。
""".strip()
    if job.get("name") == "AI用量日报-每日18点":
        prompt = "使用 ai-usage-report Skill 生成一份当天 AI 用量日报，只返回最终报告。"
    if job.get("name") in {"大米先生新店开业播报", "大米先生新店播报"}:
        prompt = prompt.replace("使用web_search", "使用 zhipu-search 或 baidu-search Skill")
    return prompt + (HOME_DELIVERY_NOTE if deliver == "feishu" else LOCAL_DELIVERY_NOTE)


def convert_schedule(schedule: dict[str, Any]) -> tuple[dict[str, Any], str]:
    kind = schedule.get("kind")
    if kind == "cron":
        expr = str(schedule.get("expr") or "").strip()
        if not expr:
            raise CronMigrationError("cron expression is empty")
        croniter(expr, datetime.now(ZoneInfo(TIMEZONE)))
        return {"kind": "cron", "expr": expr, "display": expr}, expr
    if kind == "at":
        run_at = str(schedule.get("at") or "").strip()
        if not run_at:
            raise CronMigrationError("one-shot timestamp is empty")
        parsed = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
        return {
            "kind": "once",
            "run_at": parsed.isoformat(),
            "display": f"once at {parsed.astimezone(ZoneInfo(TIMEZONE)).strftime('%Y-%m-%d %H:%M')}",
        }, run_at
    raise CronMigrationError(f"unsupported OpenClaw schedule kind: {kind}")


def next_run(schedule: dict[str, Any], enabled: bool) -> str | None:
    if not enabled or schedule.get("kind") != "cron":
        return None
    return croniter(schedule["expr"], datetime.now(ZoneInfo(TIMEZONE))).get_next(datetime).isoformat()


def converted_job(
    source_job: dict[str, Any],
    *,
    profile: str,
    resolution: str,
    source_hash: str,
) -> dict[str, Any]:
    source_id = str(source_job.get("id") or "").strip()
    name = str(source_job.get("name") or source_id).strip()
    if not source_id or not name:
        raise CronMigrationError("OpenClaw cron job is missing id/name")
    schedule, schedule_display = convert_schedule(source_job.get("schedule") or {})
    enabled = bool(source_job.get("enabled")) and schedule.get("kind") == "cron"
    deliver = "local" if name in LOCAL_DELIVERY_NAMES or name.startswith("water-reminder-test-") else "feishu"
    prompt = adapt_prompt(source_job, profile, deliver)
    created_ms = source_job.get("createdAtMs")
    try:
        created_at = datetime.fromtimestamp(int(created_ms) / 1000, timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        created_at = datetime.now(timezone.utc).isoformat()
    skills: list[str] = []
    if name == "AI用量日报-每日18点":
        skills = ["ai-usage-report"]
    elif name in {"大米先生新店开业播报", "大米先生新店播报"}:
        skills = ["zhipu-search", "baidu-search"]
    elif name in {"ai-daily-digest-9am", "iran-war-monitor"}:
        skills = ["zhipu-search", "baidu-search"]
    completed = 1 if schedule.get("kind") == "once" else 0
    paused_reason = None
    if not enabled:
        paused_reason = "expired one-shot migrated for audit" if schedule.get("kind") == "once" else "disabled in OpenClaw"
    source_job_hash = sha256_bytes(canonical_json(source_job))
    return {
        "id": f"oc-{source_id}",
        "name": name,
        "prompt": prompt,
        "skills": skills,
        "skill": skills[0] if skills else None,
        "model": None,
        "provider": None,
        "provider_snapshot": "custom",
        "model_snapshot": "deepseek-v4-flash",
        "base_url": None,
        "script": None,
        "no_agent": False,
        "monitor_script": None,
        "monitor_url": None,
        "monitor_state": None,
        "context_from": None,
        "schedule": schedule,
        "schedule_display": schedule_display,
        "repeat": {"times": 1 if schedule.get("kind") == "once" else None, "completed": completed},
        "enabled": enabled,
        "state": "scheduled" if enabled else "paused",
        "paused_at": None if enabled else created_at,
        "paused_reason": paused_reason,
        "created_at": created_at,
        "next_run_at": next_run(schedule, enabled),
        "last_run_at": None,
        "last_status": None,
        "last_error": None,
        "last_delivery_error": None,
        "failure_streak": 0,
        "deliver": deliver,
        "origin": None,
        "enabled_toolsets": None,
        "workdir": f"/opt/data/profiles/{profile}/workspace",
        "migration": {
            "schema_version": SCHEMA,
            "source": str(SOURCE_JOBS),
            "source_file_sha256": source_hash,
            "source_job_id": source_id,
            "source_job_sha256": source_job_hash,
            "profile_resolution": resolution,
            "execution_history_migrated": False,
        },
    }


def build_plan(legacy: Path, target: Path) -> dict[str, Any]:
    source_path = legacy / SOURCE_JOBS
    source_data = read_json(source_path)
    jobs = source_data.get("jobs")
    if not isinstance(jobs, list):
        raise CronMigrationError("OpenClaw jobs root is not a list")
    source_hash = sha256_bytes(source_path.read_bytes())
    identity_profiles, agent_profiles = load_identity_profiles(target)
    imports = []
    review = []
    for source_job in jobs:
        if not isinstance(source_job, dict):
            raise CronMigrationError("OpenClaw job is not an object")
        name = str(source_job.get("name") or "")
        if name in SYSTEM_REVIEW_REASONS:
            review.append({
                "id": source_job.get("id"),
                "name": name,
                "enabled": bool(source_job.get("enabled")),
                "reason": SYSTEM_REVIEW_REASONS[name],
            })
            continue
        profile, resolution = resolve_profile(source_job, identity_profiles, agent_profiles)
        profile_dir = target / "profiles" / profile
        if not profile_dir.is_dir() or profile_dir.is_symlink():
            raise CronMigrationError(f"target Profile is missing or unsafe: {profile}")
        job = converted_job(
            source_job,
            profile=profile,
            resolution=resolution,
            source_hash=source_hash,
        )
        imports.append({"profile": profile, "job": job})
    return {
        "schema_version": SCHEMA,
        "source": str(source_path),
        "source_sha256": source_hash,
        "source_jobs": len(jobs),
        "imports": imports,
        "review": review,
        "summary": {
            "imported": len(imports),
            "enabled": sum(row["job"]["enabled"] for row in imports),
            "disabled": sum(not row["job"]["enabled"] for row in imports),
            "review": len(review),
            "profiles": len({row["profile"] for row in imports}),
        },
    }


def load_hermes_jobs(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = read_json(path)
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        raise CronMigrationError(f"Hermes jobs root is not a list: {path}")
    return jobs


def update_timezone(path: Path) -> bytes:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise CronMigrationError(f"config is not an object: {path}")
    data["timezone"] = TIMEZONE
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode()


def apply_plan(plan: dict[str, Any], legacy: Path, target: Path) -> dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = target / "backups" / f"openclaw-cron-migration-{stamp}"
    backup.mkdir(parents=True, mode=0o700)
    target_stat = target.stat()
    if os.geteuid() == 0:
        os.chown(backup, target_stat.st_uid, target_stat.st_gid)

    config_paths = [target / "config.yaml", *sorted((target / "profiles").glob("*/config.yaml"))]
    for path in config_paths:
        if not path.is_file() or path.is_symlink():
            raise CronMigrationError(f"unsafe config path: {path}")
        rel = path.relative_to(target)
        destination = backup / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        stat = path.stat()
        atomic_write(path, update_timezone(path), uid=stat.st_uid, gid=stat.st_gid, mode=0o600)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in plan["imports"]:
        grouped[row["profile"]].append(row["job"])
    added = 0
    unchanged = 0
    for profile, incoming in sorted(grouped.items()):
        profile_dir = target / "profiles" / profile
        cron_dir = profile_dir / "cron"
        jobs_path = cron_dir / "jobs.json"
        profile_stat = profile_dir.stat()
        with cron_store_lock(
            cron_dir,
            uid=profile_stat.st_uid,
            gid=profile_stat.st_gid,
        ):
            existing = load_hermes_jobs(jobs_path)
            by_source = {
                (job.get("migration") or {}).get("source_job_id"): job
                for job in existing
                if isinstance(job, dict)
            }
            for job in incoming:
                source_id = job["migration"]["source_job_id"]
                prior = by_source.get(source_id)
                if prior is not None:
                    prior_hash = (prior.get("migration") or {}).get("source_job_sha256")
                    if prior_hash != job["migration"]["source_job_sha256"]:
                        raise CronMigrationError(f"existing migrated job changed: {profile}/{source_id}")
                    unchanged += 1
                    continue
                if any(item.get("id") == job["id"] for item in existing):
                    raise CronMigrationError(f"Hermes cron id collision: {profile}/{job['id']}")
                existing.append(job)
                by_source[source_id] = job
                added += 1
            if incoming and (added or not jobs_path.exists()):
                if jobs_path.exists():
                    destination = backup / jobs_path.relative_to(target)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(jobs_path, destination)
                atomic_write(
                    jobs_path,
                    canonical_json({"jobs": existing}),
                    uid=profile_stat.st_uid,
                    gid=profile_stat.st_gid,
                    mode=0o600,
                )
                os.chmod(cron_dir, 0o700)
                if os.geteuid() == 0:
                    os.chown(cron_dir, profile_stat.st_uid, profile_stat.st_gid)

    # Restore the one operational course-state file excluded by the original
    # blanket Memory archive policy. This is durable task state, not chat history.
    course_profile = next(
        (row["profile"] for row in plan["imports"] if row["job"]["name"] == "潮流计算精修课"),
        None,
    )
    support_files = []
    if course_profile:
        profile_dir = target / "profiles" / course_profile
        index = read_json(profile_dir / "legacy-memory/index.json")
        match = next(
            (
                row for row in index.get("objects") or []
                if "memory/power_flow_course.md" in (row.get("paths") or [])
            ),
            None,
        )
        if not match:
            raise CronMigrationError("course state is missing from legacy Memory archive")
        source = profile_dir / "legacy-memory/objects" / f"{match['sha256']}.md"
        destination = profile_dir / "workspace/memory/power_flow_course.md"
        if destination.exists() and sha256_bytes(destination.read_bytes()) != match["sha256"]:
            raise CronMigrationError("existing course state conflicts with archived source")
        destination.parent.mkdir(parents=True, exist_ok=True)
        profile_stat = profile_dir.stat()
        os.chmod(destination.parent, 0o700)
        if os.geteuid() == 0:
            os.chown(destination.parent, profile_stat.st_uid, profile_stat.st_gid)
        atomic_write(
            destination,
            source.read_bytes(),
            uid=profile_stat.st_uid,
            gid=profile_stat.st_gid,
            mode=0o600,
        )
        support_files.append({"profile": course_profile, "path": "workspace/memory/power_flow_course.md", "sha256": match["sha256"]})

    archive = target / "migration/openclaw-cron-source"
    archive.mkdir(parents=True, exist_ok=True)
    for relative in (SOURCE_JOBS, SOURCE_STATE):
        source = legacy / relative
        if source.is_file() and not source.is_symlink():
            destination = archive / source.name
            atomic_write(
                destination,
                source.read_bytes(),
                uid=target_stat.st_uid,
                gid=target_stat.st_gid,
                mode=0o440,
            )
    public_plan = copy.deepcopy(plan)
    for row in public_plan["imports"]:
        row["job"].pop("prompt", None)
    plan_path = target / "migration/openclaw-cron-migration-manifest.json"
    atomic_write(
        plan_path,
        canonical_json(public_plan),
        uid=target_stat.st_uid,
        gid=target_stat.st_gid,
        mode=0o440,
    )
    receipt = {
        "schema_version": SCHEMA,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": plan["source_sha256"],
        "source_jobs": plan["source_jobs"],
        "jobs_added": added,
        "jobs_unchanged": unchanged,
        "summary": plan["summary"],
        "review": plan["review"],
        "execution_history_migrated": False,
        "timezone": TIMEZONE,
        "support_files": support_files,
        "backup": str(backup),
    }
    receipt_path = target / "migration/openclaw-cron-migration-receipt.json"
    atomic_write(
        receipt_path,
        canonical_json(receipt),
        uid=target_stat.st_uid,
        gid=target_stat.st_gid,
        mode=0o440,
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-root", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    legacy = args.legacy_root.resolve(strict=True)
    target = args.target.resolve(strict=True)
    plan = build_plan(legacy, target)
    result = apply_plan(plan, legacy, target) if args.apply else {
        "schema_version": SCHEMA,
        "source_sha256": plan["source_sha256"],
        "summary": plan["summary"],
        "review": plan["review"],
        "apply": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
