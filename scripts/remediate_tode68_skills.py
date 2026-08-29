#!/usr/bin/env python3
"""Activate the reviewed TODE68 skills and migrate their runtime prerequisites."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml


SKILLS = {
    "ai-usage-report",
    "baidu-baike-data",
    "baidu-scholar-search-skill",
    "baidu-search",
    "best-image-generation",
    "gemini-image-simple",
    "lark-bot-ops",
    "nano-banana-openrouter",
    "qwen-image",
    "ragflow-skill",
    "self-improving",
    "service-status-timeline",
    "skill-vetter",
    "teap-cli-ops",
    "teap-test",
    "tode-jira",
    "zhipu-search",
}
SKILL_ENV_KEYS = {
    "AI_USAGE_API_TOKEN",
    "AI_USAGE_ENDPOINT_URL",
    "BAIDU_API_KEY",
    "DASHSCOPE_API_KEY",
    "EVOLINK_API_KEY",
    "GEMINI_API_KEY",
    "GITEA_TOKEN",
    "JIRA_API_TOKEN",
    "JIRA_AUTH_TYPE",
    "JIRA_CONFIG_FILE",
    "JIRA_SERVER",
    "OPENROUTER_API_KEY",
    "RAGFLOW_API_KEY",
    "RAGFLOW_API_URL",
    "ZHIPU_API_KEY",
}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file() or path.is_symlink():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key and value:
            values[key] = value
    return values


def render_env(values: dict[str, str]) -> bytes:
    lines = []
    for key in sorted(values):
        value = values[key].replace("\n", "").replace("\r", "")
        lines.append(f"{key}={value}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def atomic_write(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def load_legacy_skill_env(legacy: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    config_path = legacy / "openclaw.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    entries = ((config.get("skills") or {}).get("entries") or {})
    for item in entries.values():
        if isinstance(item, dict):
            env = item.get("env") or {}
            if isinstance(env, dict):
                for key, value in env.items():
                    if key in SKILL_ENV_KEYS and isinstance(value, str) and value:
                        result[key] = value
    for path in (
        legacy / ".env",
        legacy / "workspace/.env",
        legacy / "skills/zhipu-search/skill.env",
    ):
        for key, value in parse_env(path).items():
            if key in SKILL_ENV_KEYS:
                result[key] = value
    result.setdefault("JIRA_AUTH_TYPE", "bearer")
    result.setdefault("JIRA_SERVER", "https://jira.tode.ltd")
    result["JIRA_CONFIG_FILE"] = "/opt/data/tool-config/jira/config.yml"
    return result


def tea_token(path: Path) -> str | None:
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for login in data.get("logins") or []:
        if isinstance(login, dict) and login.get("token"):
            return str(login["token"])
    return None


def copy_tree(source: Path, target: Path) -> None:
    if source.is_symlink() or any(path.is_symlink() for path in source.rglob("*")):
        raise RuntimeError(f"symlink rejected: {source}")
    staging = target.with_name(f".{target.name}.remediation-{os.getpid()}")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(source, staging)
    if target.exists():
        shutil.rmtree(target)
    os.replace(staging, target)


def harden_tree(root: Path, uid: int, gid: int) -> None:
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            raise RuntimeError(f"symlink rejected after copy: {path}")
        os.chown(path, uid, gid)
        if path.is_dir():
            path.chmod(0o550)
        elif path.is_file():
            executable = path.suffix in {".sh"} or path.parent.name == "bin"
            path.chmod(0o550 if executable else 0o440)


def private_writable_tree(root: Path, uid: int, gid: int) -> None:
    """Keep credential/config state private while allowing the CLI to refresh it."""
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            raise RuntimeError(f"symlink rejected in private config: {path}")
        os.chown(path, uid, gid)
        path.chmod(0o700 if path.is_dir() else 0o600)


def update_config(path: Path, *, profile: bool = False) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    approvals = data.setdefault("approvals", {})
    approvals["destructive_slash_confirm"] = False
    if profile:
        feishu = (data.get("platforms") or {}).get("feishu")
        if isinstance(feishu, dict) and feishu.get("home_channel"):
            feishu.pop("enabled", None)
    atomic_write(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(args: argparse.Namespace) -> dict[str, object]:
    target = args.target.resolve(strict=True)
    source = args.skills_source.resolve(strict=True)
    legacy = args.legacy_root.resolve(strict=True)
    actual = {path.name for path in source.iterdir() if path.is_dir()}
    if actual != SKILLS:
        raise RuntimeError(f"unexpected skill set: {sorted(actual ^ SKILLS)}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = target / "backups" / f"skill-remediation-{stamp}"
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(target / "config.yaml", backup / "config.yaml")

    credentials = load_legacy_skill_env(legacy)
    token = tea_token(args.tea_config)
    if token:
        credentials["GITEA_TOKEN"] = token

    profiles = sorted(path for path in (target / "profiles").iterdir() if path.is_dir())
    for profile in profiles:
        profile_backup = backup / "profiles" / profile.name
        profile_backup.mkdir(parents=True, exist_ok=True)
        for name in ("config.yaml", ".env"):
            source_path = profile / name
            if source_path.is_file() and not source_path.is_symlink():
                shutil.copy2(source_path, profile_backup / name)
    destinations = [target / "shared-skills"] + [profile / "skills" for profile in profiles]
    for destination in destinations:
        destination.mkdir(parents=True, exist_ok=True)
        for name in sorted(SKILLS):
            copy_tree(source / name, destination / name)

    update_config(target / "config.yaml")
    for profile in profiles:
        update_config(profile / "config.yaml", profile=True)
    for profile in profiles:
        env_path = profile / ".env"
        env = parse_env(env_path)
        env.update(credentials)
        atomic_write(env_path, render_env(env))

    tool_bin = target / "bin"
    tool_bin.mkdir(parents=True, exist_ok=True)
    for name in ("jira", "lark-cli", "teap"):
        src = args.tool_source / name
        if not src.is_file() or src.is_symlink():
            raise RuntimeError(f"missing regular tool binary: {src}")
        shutil.copy2(src, tool_bin / name, follow_symlinks=False)
        (tool_bin / name).chmod(0o550)

    tool_config = target / "tool-config"
    (tool_config / "jira").mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.jira_config, tool_config / "jira/config.yml")
    teap_target = target / ".config/teap-cli"
    teap_target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.teap_config, teap_target / "config.json")
    for src, dst in (
        (args.lark_config, target / ".lark-cli"),
        (args.lark_share, target / ".local/share/lark-cli"),
    ):
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, symlinks=False)

    bind_env = os.environ.copy()
    bind_env.update({"HOME": str(target), "HERMES_HOME": str(target)})
    bind = subprocess.run(
        [str(tool_bin / "lark-cli"), "config", "bind", "--source", "hermes"],
        env=bind_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if bind.returncode != 0:
        raise RuntimeError(f"lark-cli Hermes bind failed: {bind.stderr.strip()[:300]}")

    for path in destinations + [tool_bin, tool_config, target / ".config"]:
        harden_tree(path, args.uid, args.gid)
    private_writable_tree(target / ".lark-cli", args.uid, args.gid)
    private_writable_tree(target / ".local/share/lark-cli", args.uid, args.gid)
    for profile in profiles:
        os.chown(profile / ".env", args.uid, args.gid)
        (profile / ".env").chmod(0o600)
        os.chown(profile / "config.yaml", args.uid, args.gid)
        (profile / "config.yaml").chmod(0o600)
    os.chown(target / "config.yaml", args.uid, args.gid)
    (target / "config.yaml").chmod(0o600)

    configured = sorted(key for key in SKILL_ENV_KEYS if credentials.get(key))
    missing = sorted(
        key
        for key in ("DASHSCOPE_API_KEY", "EVOLINK_API_KEY", "GEMINI_API_KEY")
        if not credentials.get(key)
    )
    receipt = {
        "schema_version": "tode68-skill-remediation/v1",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "skills_activated": len(SKILLS),
        "skill_names": sorted(SKILLS),
        "profiles_updated": len(profiles),
        "approval_confirmation_disabled": True,
        "configured_environment_keys": configured,
        "missing_optional_environment_keys": missing,
        "tools": {
            name: {"sha256": sha256(tool_bin / name), "path": f"/opt/data/bin/{name}"}
            for name in ("jira", "lark-cli", "teap")
        },
        "backup": str(backup),
    }
    atomic_write(
        target / "migration/skill-remediation-receipt.json",
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n",
        0o440,
    )
    return receipt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--target", type=Path, default=Path("/opt/data"))
    result.add_argument("--skills-source", type=Path, default=Path("/opt/hermes/deploy/tode68/shared-skills"))
    result.add_argument("--legacy-root", type=Path, default=Path("/srv/openclaw"))
    result.add_argument("--tool-source", type=Path, default=Path("/legacy/bin"))
    result.add_argument("--tea-config", type=Path, default=Path("/legacy/tea/config.yml"))
    result.add_argument("--jira-config", type=Path, default=Path("/legacy/jira/.config.yml"))
    result.add_argument("--teap-config", type=Path, default=Path("/legacy/teap/config.json"))
    result.add_argument("--lark-config", type=Path, default=Path("/legacy/lark/config"))
    result.add_argument("--lark-share", type=Path, default=Path("/legacy/lark/share"))
    result.add_argument("--uid", type=int, default=10000)
    result.add_argument("--gid", type=int, default=10000)
    return result


if __name__ == "__main__":
    print(json.dumps(run(parser().parse_args()), ensure_ascii=False, indent=2, sort_keys=True))
