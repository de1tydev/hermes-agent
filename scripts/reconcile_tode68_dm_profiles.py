#!/usr/bin/env python3
"""Reconcile TODE68 Feishu DM identity drift and native Zhipu MCP config."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


SCHEMA = "tode68-feishu-dm-reconciliation/v1"
REGISTRY_SCHEMA = "hermes-profile-identity-registry/v1"
ZHIPU_MCP_SERVERS = {
    "zhipu-web-search": {
        "url": "https://open.bigmodel.cn/api/mcp/web_search_prime/mcp",
        "headers": {"Authorization": "Bearer ${ZHIPU_API_KEY}"},
    },
    "zhipu-web-reader": {
        "url": "https://open.bigmodel.cn/api/mcp/web_reader/mcp",
        "headers": {"Authorization": "Bearer ${ZHIPU_API_KEY}"},
    },
    "zhipu-zread": {
        "url": "https://open.bigmodel.cn/api/mcp/zread/mcp",
        "headers": {"Authorization": "Bearer ${ZHIPU_API_KEY}"},
    },
}


class ReconciliationError(RuntimeError):
    pass


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ReconciliationError(f"required regular JSON missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReconciliationError(f"JSON root is not an object: {path}")
    return value


def parse_env(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def render_env(values: dict[str, str]) -> bytes:
    return ("\n".join(f"{key}={values[key]}" for key in sorted(values)) + "\n").encode()


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


def identity_digest(kind: str, identity: str) -> str:
    return hashlib.sha256(f"feishu\0{kind}\0{identity}".encode()).hexdigest()


def alias_key(app_id: str, alias_kind: str, identity: str) -> tuple[str, str]:
    digest = hashlib.sha256(
        f"feishu\0dm-alias\0{app_id}\0{alias_kind}\0{identity}".encode()
    ).hexdigest()
    return f"feishu:dm-alias:{alias_kind}:sha256:{digest}", f"sha256:{digest}"


def chat_key(chat_id: str) -> tuple[str, str]:
    digest = identity_digest("dm", chat_id)
    return f"feishu:dm:sha256:{digest}", f"sha256:{digest}"


class FeishuApp:
    def __init__(self, label: str, env_path: Path):
        values = parse_env(env_path)
        self.label = label
        self.app_id = values["FEISHU_APP_ID"]
        status, response = self._request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            method="POST",
            body={
                "app_id": self.app_id,
                "app_secret": values["FEISHU_APP_SECRET"],
            },
            authenticated=False,
        )
        self.token = response.get("tenant_access_token")
        if status != 200 or not self.token:
            raise ReconciliationError(f"could not authenticate Feishu app {label}")

    def _request(
        self,
        url: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        authenticated: bool = True,
    ) -> tuple[int, dict[str, Any]]:
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read())
            except Exception:
                return exc.code, {}

    def resolve_open_id(self, open_id: str) -> dict[str, str] | None:
        status, response = self._request(
            "https://open.feishu.cn/open-apis/contact/v3/users/"
            + open_id
            + "?user_id_type=open_id"
        )
        user = ((response.get("data") or {}).get("user") or {})
        if status != 200 or response.get("code") != 0:
            return None
        return {
            "open_id": str(user.get("open_id") or ""),
            "user_id": str(user.get("user_id") or ""),
            "union_id": str(user.get("union_id") or ""),
            "name": str(user.get("name") or ""),
        }

    def can_read_message(self, message_id: str) -> bool:
        status, response = self._request(
            "https://open.feishu.cn/open-apis/im/v1/messages/" + message_id
        )
        return bool(
            status == 200
            and response.get("code") == 0
            and ((response.get("data") or {}).get("items") or [])
        )


def profile_for_open_id(target: Path, open_id: str) -> str:
    digest = identity_digest("dm", open_id)
    manifest = read_json(target / "migration/migration-manifest.json")
    matches = [
        row["profile"]
        for row in manifest.get("profiles") or []
        if isinstance(row, dict)
        and row.get("kind") == "dm"
        and str(row.get("identity_digest") or "").removeprefix("sha256:")
        == digest
    ]
    if len(matches) != 1:
        raise ReconciliationError(f"legacy DM route has {len(matches)} Profiles")
    return matches[0]


def latest_dm_source(profile: Path) -> dict[str, str] | None:
    db_path = profile / "state.db"
    if not db_path.is_file():
        return None
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT origin_json FROM sessions WHERE source='feishu' AND chat_type='dm' "
            "ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        source = json.loads(row[0])
        message = connection.execute(
            "SELECT platform_message_id FROM messages WHERE role='user' "
            "AND platform_message_id IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "chat_id": str(source.get("chat_id") or ""),
            "user_id": str(source.get("user_id") or ""),
            "union_id": str(source.get("user_id_alt") or ""),
            "user_name": str(source.get("user_name") or ""),
            "message_id": str(message[0] if message else ""),
        }
    finally:
        connection.close()


def build_plan(legacy: Path, target: Path) -> dict[str, Any]:
    apps = [
        FeishuApp("primary", target / ".env"),
        FeishuApp("secondary", target / "gateway-secondary.env"),
    ]
    users = read_json(legacy / "automation/auto-user-isolation/users.json")

    def resolve(item: tuple[str, str]) -> dict[str, Any]:
        raw, agent = item
        open_id = raw.removeprefix("feishu:")
        for app in apps:
            identity = app.resolve_open_id(open_id)
            if identity is not None:
                return {
                    "agent": agent,
                    "app_label": app.label,
                    "app_id": app.app_id,
                    "profile": profile_for_open_id(target, open_id),
                    **identity,
                }
        raise ReconciliationError("one legacy DM identity could not be resolved")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        routes = list(executor.map(resolve, users.items()))

    aliases: dict[str, dict[str, Any]] = {}
    raw_alias_lookup: dict[tuple[str, str, str], str] = {}
    for route in routes:
        for alias_kind in ("user_id", "union_id"):
            identity = route[alias_kind]
            if not identity:
                continue
            key, digest = alias_key(route["app_id"], alias_kind, identity)
            existing = aliases.get(key)
            row = {
                "platform": "feishu",
                "kind": "dm",
                "alias_kind": alias_kind,
                "alias_digest": digest,
                "profile": route["profile"],
                "claimed_chat_digest": None,
            }
            if existing is not None and existing["profile"] != row["profile"]:
                raise ReconciliationError("legacy DM alias maps to multiple Profiles")
            aliases[key] = row
            raw_alias_lookup[(route["app_id"], alias_kind, identity)] = route["profile"]

    reconciliations = []
    for profile in sorted((target / "profiles").iterdir()):
        if not (profile / ".hermes-auto-profile.json").is_file():
            continue
        source = latest_dm_source(profile)
        if not source or not source["chat_id"] or not source["message_id"]:
            continue
        readable = [app for app in apps if app.can_read_message(source["message_id"])]
        if len(readable) != 1:
            raise ReconciliationError(
                f"could not resolve one owning Feishu app for {profile.name}"
            )
        app = readable[0]
        candidates = {
            raw_alias_lookup[(app.app_id, alias_kind, source[alias_kind])]
            for alias_kind in ("user_id", "union_id")
            if source[alias_kind]
            and (app.app_id, alias_kind, source[alias_kind]) in raw_alias_lookup
        }
        if not candidates:
            continue  # a genuinely new chat
        if len(candidates) != 1:
            raise ReconciliationError("active DM aliases disagree")
        source_profile = next(iter(candidates))
        if source_profile == profile.name:
            continue
        reconciliations.append(
            {
                "source_profile": source_profile,
                "target_profile": profile.name,
                "app_id": app.app_id,
                "chat_id": source["chat_id"],
                "user_id": source["user_id"],
                "union_id": source["union_id"],
                "user_name": source["user_name"],
            }
        )

    if len({row["source_profile"] for row in reconciliations}) != len(reconciliations):
        raise ReconciliationError("one imported Profile would merge more than once")
    return {
        "schema_version": SCHEMA,
        "routes": routes,
        "aliases": aliases,
        "reconciliations": reconciliations,
        "summary": {
            "legacy_dm_routes": len(routes),
            "primary_routes": sum(row["app_label"] == "primary" for row in routes),
            "secondary_routes": sum(row["app_label"] == "secondary" for row in routes),
            "legacy_aliases": len(aliases),
            "active_profile_reconciliations": len(reconciliations),
        },
    }


def copy_merge(source: Path, target: Path, *, uid: int, gid: int) -> int:
    copied = 0
    if not source.exists():
        return copied
    if source.is_symlink() or any(path.is_symlink() for path in source.rglob("*")):
        raise ReconciliationError(f"symlink rejected in merge source: {source}")
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        if destination.exists():
            if not destination.is_file() or sha256_bytes(destination.read_bytes()) != sha256_bytes(path.read_bytes()):
                raise ReconciliationError(f"durable Profile merge conflict: {relative}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination, follow_symlinks=False)
        if os.geteuid() == 0:
            os.chown(destination, uid, gid)
        copied += 1
    for directory in [target, *[p for p in target.rglob("*") if p.is_dir()]]:
        if os.geteuid() == 0:
            os.chown(directory, uid, gid)
    return copied


def update_mcp_config(path: Path) -> bytes:
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config.setdefault("mcp_servers", {}).update(ZHIPU_MCP_SERVERS)
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode()


def merge_profile(source: Path, target: Path) -> dict[str, Any]:
    target_stat = target.stat()
    copied = 0
    for name in ("memories", "legacy-memory", "workspace"):
        copied += copy_merge(
            source / name,
            target / name,
            uid=target_stat.st_uid,
            gid=target_stat.st_gid,
        )

    legacy_skill_source = source / "skills/legacy-memory-search"
    legacy_skill_target = target / "skills/legacy-memory-search"
    legacy_memory_search_restored = False
    if legacy_skill_source.is_dir():
        target_existed = legacy_skill_target.exists()
        copied += copy_merge(
            legacy_skill_source,
            legacy_skill_target,
            uid=target_stat.st_uid,
            gid=target_stat.st_gid,
        )
        legacy_memory_search_restored = not target_existed

    source_env = parse_env(source / ".env")
    target_env = parse_env(target / ".env")
    for key, value in source_env.items():
        if key in target_env and target_env[key] != value:
            raise ReconciliationError(f"Profile env conflict: {key}")
        target_env[key] = value
    atomic_write(
        target / ".env",
        render_env(target_env),
        uid=target_stat.st_uid,
        gid=target_stat.st_gid,
        mode=0o600,
    )

    source_config = yaml.safe_load((source / "config.yaml").read_text()) or {}
    target_config = yaml.safe_load((target / "config.yaml").read_text()) or {}
    target_home = (((target_config.get("platforms") or {}).get("feishu") or {}).get("home_channel"))
    source_config.setdefault("terminal", {})["cwd"] = str(target / "workspace")
    feishu = source_config.setdefault("platforms", {}).setdefault("feishu", {})
    feishu.pop("enabled", None)
    if target_home:
        feishu["home_channel"] = target_home
    source_config.setdefault("mcp_servers", {}).update(ZHIPU_MCP_SERVERS)
    atomic_write(
        target / "config.yaml",
        yaml.safe_dump(source_config, allow_unicode=True, sort_keys=False).encode(),
        uid=target_stat.st_uid,
        gid=target_stat.st_gid,
        mode=0o600,
    )
    if (source / "SOUL.md").is_file():
        atomic_write(
            target / "SOUL.md",
            (source / "SOUL.md").read_bytes(),
            uid=target_stat.st_uid,
            gid=target_stat.st_gid,
            mode=0o600,
        )

    source_jobs_path = source / "cron/jobs.json"
    target_jobs_path = target / "cron/jobs.json"
    moved_jobs = 0
    if source_jobs_path.is_file():
        source_jobs = read_json(source_jobs_path).get("jobs") or []
        target_jobs = read_json(target_jobs_path).get("jobs") or [] if target_jobs_path.is_file() else []
        existing = {job.get("id"): job for job in target_jobs}
        for job in source_jobs:
            migrated = json.loads(json.dumps(job))
            workdir = migrated.get("workdir")
            if isinstance(workdir, str):
                migrated["workdir"] = workdir.replace(str(source), str(target), 1)
            prior = existing.get(migrated.get("id"))
            if prior is not None and canonical_json(prior) != canonical_json(migrated):
                raise ReconciliationError("cron job collision during Profile merge")
            if prior is None:
                target_jobs.append(migrated)
                existing[migrated.get("id")] = migrated
                moved_jobs += 1
        atomic_write(
            target_jobs_path,
            canonical_json({"jobs": target_jobs}),
            uid=target_stat.st_uid,
            gid=target_stat.st_gid,
            mode=0o600,
        )
        source_stat = source.stat()
        atomic_write(
            source_jobs_path,
            canonical_json({"jobs": []}),
            uid=source_stat.st_uid,
            gid=source_stat.st_gid,
            mode=0o600,
        )
    return {
        "files_copied": copied,
        "cron_jobs_moved": moved_jobs,
        "legacy_memory_search_restored": legacy_memory_search_restored,
    }


def apply_plan(plan: dict[str, Any], target: Path) -> dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = target / "backups" / f"dm-reconciliation-{stamp}"
    backup.mkdir(parents=True, mode=0o700)
    target_stat = target.stat()
    if os.geteuid() == 0:
        os.chown(backup, target_stat.st_uid, target_stat.st_gid)

    registry_path = target / "state/profile-identity-registry.json"
    shutil.copy2(registry_path, backup / "profile-identity-registry.json")
    for name in ("config.yaml", ".env"):
        shutil.copy2(target / name, backup / name)
    configs = [target / "config.yaml", *sorted((target / "profiles").glob("*/config.yaml"))]
    config_backup = backup / "configs"
    for path in configs:
        destination = config_backup / path.relative_to(target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    for row in plan["reconciliations"]:
        for key in ("source_profile", "target_profile"):
            source = target / "profiles" / row[key]
            destination = backup / "profiles" / row[key]
            shutil.copytree(source, destination, symlinks=False)

    # Native MCP config for every current and future Profile.
    for path in configs:
        stat = path.stat()
        atomic_write(
            path,
            update_mcp_config(path),
            uid=stat.st_uid,
            gid=stat.st_gid,
            mode=0o600,
        )
    profile_keys = []
    for path in sorted((target / "profiles").glob("*/.env")):
        values = parse_env(path)
        if values.get("ZHIPU_API_KEY"):
            profile_keys.append(values["ZHIPU_API_KEY"])
    if not profile_keys or len(set(profile_keys)) != 1:
        raise ReconciliationError("Profile ZHIPU_API_KEY values are missing or inconsistent")
    root_env = parse_env(target / ".env")
    root_env["ZHIPU_API_KEY"] = profile_keys[0]
    atomic_write(
        target / ".env",
        render_env(root_env),
        uid=target_stat.st_uid,
        gid=target_stat.st_gid,
        mode=0o600,
    )

    registry = read_json(registry_path)
    if registry.get("schema_version") != REGISTRY_SCHEMA:
        raise ReconciliationError("unexpected Profile registry schema")
    registry["legacy_dm_aliases"] = plan["aliases"]
    merge_results = []
    for row in plan["reconciliations"]:
        source = target / "profiles" / row["source_profile"]
        destination = target / "profiles" / row["target_profile"]
        result = merge_profile(source, destination)
        chat_binding_key, chat_digest = chat_key(row["chat_id"])
        registry["bindings"][chat_binding_key] = {
            "platform": "feishu",
            "kind": "dm",
            "identity_digest": chat_digest,
            "profile": row["target_profile"],
        }
        stale = [
            key for key, binding in registry["bindings"].items()
            if binding.get("profile") == row["target_profile"]
            and binding.get("kind") == "dm"
            and key != chat_binding_key
        ]
        for key in stale:
            registry["bindings"].pop(key)
        for alias_kind in ("user_id", "union_id"):
            identity = row[alias_kind]
            if not identity:
                continue
            key, _digest = alias_key(row["app_id"], alias_kind, identity)
            alias = registry["legacy_dm_aliases"].get(key)
            if alias is None:
                raise ReconciliationError("active Profile alias is missing")
            alias["profile"] = row["target_profile"]
            alias["claimed_chat_digest"] = chat_digest
        merge_results.append({
            "source_profile": row["source_profile"],
            "target_profile": row["target_profile"],
            **result,
        })
    atomic_write(
        registry_path,
        canonical_json(registry),
        uid=target_stat.st_uid,
        gid=target_stat.st_gid,
        mode=0o600,
    )
    receipt = {
        "schema_version": SCHEMA,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "summary": plan["summary"],
        "merge_results": merge_results,
        "registry_bindings": len(registry["bindings"]),
        "legacy_dm_aliases": len(registry["legacy_dm_aliases"]),
        "native_zhipu_mcp_servers": sorted(ZHIPU_MCP_SERVERS),
        "root_zhipu_key_configured": True,
        "backup": str(backup),
    }
    receipt_path = target / "migration/dm-profile-reconciliation-receipt.json"
    atomic_write(
        receipt_path,
        canonical_json(receipt),
        uid=target_stat.st_uid,
        gid=target_stat.st_gid,
        mode=0o440,
    )
    return receipt


def public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": plan["schema_version"],
        "summary": plan["summary"],
        "reconciliations": [
            {
                "source_profile": row["source_profile"],
                "target_profile": row["target_profile"],
                "user_name": row["user_name"],
            }
            for row in plan["reconciliations"]
        ],
        "apply": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-root", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    legacy = args.legacy_root.resolve(strict=True)
    target = args.target.resolve(strict=True)
    plan = build_plan(legacy, target)
    result = apply_plan(plan, target) if args.apply else public_plan(plan)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
