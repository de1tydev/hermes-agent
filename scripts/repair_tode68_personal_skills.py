#!/usr/bin/env python3
"""补齐 TODE68 遗漏的外挂 personal Skills 和 Ask Code 调用环境。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


PERSONAL_SKILLS = {
    "ask-code",
    "lark-approval",
    "lark-attendance",
    "lark-base",
    "lark-calendar",
    "lark-contact",
    "lark-doc",
    "lark-drive",
    "lark-event",
    "lark-im",
    "lark-mail",
    "lark-minutes",
    "lark-okr",
    "lark-openapi-explorer",
    "lark-shared",
    "lark-sheets",
    "lark-skill-maker",
    "lark-slides",
    "lark-task",
    "lark-vc",
    "lark-whiteboard",
    "lark-wiki",
    "lark-workflow-meeting-summary",
    "lark-workflow-standup-report",
}
ASK_CODE_ENV_KEYS = {"ASK_CODE_URL", "ASK_CODE_API_KEY"}
RUNTIME_ENVIRONMENT = {"DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1"}
FORBIDDEN_SOURCE_NAMES = {".env", "skill.env", ".ask_code_state.json"}
SECRET_LITERAL = re.compile(
    rb"\b(?:sk|xox[baprs]|gh[pousr])-[A-Za-z0-9_-]{8,}"
    rb"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)


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
    temporary = Path(raw)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def validate_skill_source(source: Path) -> None:
    actual = {path.name for path in source.iterdir() if path.is_dir()}
    if actual != PERSONAL_SKILLS:
        raise RuntimeError(f"unexpected personal Skill set: {sorted(actual ^ PERSONAL_SKILLS)}")
    for name in sorted(PERSONAL_SKILLS):
        skill = source / name
        if not (skill / "SKILL.md").is_file():
            raise RuntimeError(f"missing SKILL.md: {name}")
        for path in skill.rglob("*"):
            if path.is_symlink():
                raise RuntimeError(f"symlink rejected: {path}")
            if path.name in FORBIDDEN_SOURCE_NAMES:
                raise RuntimeError(f"credential or state file rejected: {path}")
            if path.is_file() and path.stat().st_size <= 2_000_000:
                if SECRET_LITERAL.search(path.read_bytes()):
                    raise RuntimeError(f"secret-like literal rejected: {path}")


def validate_single_skill_source(source: Path, expected_name: str) -> None:
    if source.name != expected_name or not (source / "SKILL.md").is_file():
        raise RuntimeError(f"invalid {expected_name} Skill source: {source}")
    for path in source.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"symlink rejected: {path}")
        if path.name in FORBIDDEN_SOURCE_NAMES:
            raise RuntimeError(f"credential or state file rejected: {path}")
        if path.is_file() and path.stat().st_size <= 2_000_000:
            if SECRET_LITERAL.search(path.read_bytes()):
                raise RuntimeError(f"secret-like literal rejected: {path}")


def ask_code_environment(personal_source: Path) -> dict[str, str]:
    values = parse_env(personal_source / "ask-code/.env")
    result = {key: values.get(key, "") for key in ASK_CODE_ENV_KEYS}
    missing = sorted(key for key, value in result.items() if not value)
    if missing:
        raise RuntimeError(f"Ask Code environment is incomplete: {','.join(missing)}")
    parsed = urlsplit(result["ASK_CODE_URL"])
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("ASK_CODE_URL must be an absolute HTTP(S) URL")
    return result


def harden_tree(root: Path, uid: int, gid: int) -> None:
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            raise RuntimeError(f"symlink rejected after copy: {path}")
        os.chown(path, uid, gid)
        path.chmod(0o550 if path.is_dir() else 0o440)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_binary(source: Path, target: Path, uid: int, gid: int) -> bool:
    if not source.is_file() or source.is_symlink():
        raise RuntimeError(f"missing regular binary: {source}")
    if target.is_file() and sha256_file(source) == sha256_file(target):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(raw)
    try:
        with source.open("rb") as src, os.fdopen(fd, "wb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        os.chown(temporary, uid, gid)
        temporary.chmod(0o550)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def install_tree(source: Path, target: Path, uid: int, gid: int) -> bool:
    if target.is_dir() and tree_digest(source) == tree_digest(target):
        return False
    staging = target.with_name(f".{target.name}.personal-repair-{os.getpid()}")
    previous = target.with_name(f".{target.name}.previous-{os.getpid()}")
    if staging.exists() or previous.exists():
        raise RuntimeError(f"staging collision for {target}")
    shutil.copytree(source, staging)
    harden_tree(staging, uid, gid)
    if target.exists():
        os.replace(target, previous)
    try:
        os.replace(staging, target)
    except Exception:
        if previous.exists() and not target.exists():
            os.replace(previous, target)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    if previous.exists():
        shutil.rmtree(previous)
    return True


def run(args: argparse.Namespace) -> dict[str, object]:
    target = args.target.resolve(strict=True)
    skills_source = args.skills_source.resolve(strict=True)
    personal_source = args.personal_skills_source.resolve(strict=True)
    officecli_binary = args.officecli_binary.resolve(strict=True)
    officecli_skill = args.officecli_skill_source.resolve(strict=True)
    validate_skill_source(skills_source)
    validate_single_skill_source(officecli_skill, "officecli")
    ask_env = ask_code_environment(personal_source)

    profiles = sorted(path for path in (target / "profiles").iterdir() if path.is_dir())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = target / "backups" / f"personal-skill-repair-{stamp}-{os.getpid()}"
    backup.mkdir(parents=True, mode=0o700)

    destinations = [("shared", target / "shared-skills")]
    destinations.extend((profile.name, profile / "skills") for profile in profiles)
    for owner, destination in destinations:
        for name in sorted(PERSONAL_SKILLS | {"officecli"}):
            current = destination / name
            if not current.exists():
                continue
            saved = backup / "skills" / owner / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(current, saved)

    for profile in profiles:
        env_path = profile / ".env"
        if not env_path.is_file() or env_path.is_symlink():
            raise RuntimeError(f"missing regular Profile environment: {env_path}")
        saved = backup / "profiles" / profile.name / ".env"
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(env_path, saved)

    officecli_target = target / "bin/officecli"
    if officecli_target.exists():
        saved = backup / "bin/officecli"
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(officecli_target, saved)

    changed = 0
    skill_hashes = {name: tree_digest(skills_source / name) for name in PERSONAL_SKILLS}
    for _owner, destination in destinations:
        destination.mkdir(parents=True, exist_ok=True)
        for name in sorted(PERSONAL_SKILLS):
            if install_tree(skills_source / name, destination / name, args.uid, args.gid):
                changed += 1

    officecli_skill_changed = 0
    for _owner, destination in destinations:
        if install_tree(
            officecli_skill,
            destination / "officecli",
            args.uid,
            args.gid,
        ):
            officecli_skill_changed += 1

    for profile in profiles:
        env_path = profile / ".env"
        values = parse_env(env_path)
        values.update(ask_env)
        values.update(RUNTIME_ENVIRONMENT)
        atomic_write(env_path, render_env(values), 0o600)
        os.chown(env_path, args.uid, args.gid)

    officecli_changed = install_binary(
        officecli_binary,
        officecli_target,
        args.uid,
        args.gid,
    )

    receipt = {
        "schema_version": "tode68-personal-skill-repair/v1",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "skills_activated": len(PERSONAL_SKILLS),
        "skill_names": sorted(PERSONAL_SKILLS),
        "skill_sha256": dict(sorted(skill_hashes.items())),
        "profiles_updated": len(profiles),
        "destinations_changed": changed,
        "configured_environment_keys": sorted(
            ASK_CODE_ENV_KEYS | set(RUNTIME_ENVIRONMENT)
        ),
        "officecli": {
            "binary_changed": officecli_changed,
            "binary_path": "/opt/data/bin/officecli",
            "binary_sha256": sha256_file(officecli_target),
            "skill_destinations_changed": officecli_skill_changed,
            "skill_sha256": tree_digest(officecli_skill),
        },
        "backup": str(backup),
    }
    receipt_path = target / "migration/personal-skill-repair-receipt.json"
    atomic_write(
        receipt_path,
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n",
        0o440,
    )
    os.chown(receipt_path, args.uid, args.gid)
    return receipt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--target", type=Path, default=Path("/opt/data"))
    result.add_argument(
        "--skills-source",
        type=Path,
        default=Path("/opt/hermes/deploy/tode68/personal-skills"),
    )
    result.add_argument(
        "--personal-skills-source",
        type=Path,
        default=Path("/legacy/personal-skills"),
    )
    result.add_argument(
        "--officecli-binary",
        type=Path,
        default=Path("/legacy/bin/officecli"),
    )
    result.add_argument(
        "--officecli-skill-source",
        type=Path,
        default=Path("/opt/hermes/deploy/tode68/runtime-skills/officecli"),
    )
    result.add_argument("--uid", type=int, default=10000)
    result.add_argument("--gid", type=int, default=10000)
    return result


if __name__ == "__main__":
    print(json.dumps(run(parser().parse_args()), ensure_ascii=False, indent=2, sort_keys=True))
