#!/usr/bin/env python3
"""Move byte-identical Profile skill copies to one shared external directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

import yaml

from utils import atomic_yaml_write


WORKSPACE_RULES = """## 文件写入目录

- 所有持久文件只能写入当前 Profile 的 `$HERMES_HOME/workspace`。
- 临时文件统一写入 `$HERMES_HOME/workspace/tmp`；使用前先创建该目录。
- 不得使用 `/tmp`，不得关闭或放宽 `HERMES_WRITE_SAFE_ROOT`。
"""


def _skill_roots(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    if not root.is_dir():
        return result
    for manifest in sorted(root.rglob("SKILL.md")):
        skill_dir = manifest.parent
        if skill_dir.is_symlink() or any(path.is_symlink() for path in skill_dir.rglob("*")):
            raise RuntimeError(f"skill contains a symlink: {skill_dir}")
        result[str(skill_dir.relative_to(root))] = skill_dir
    return result


def _skill_name(skill_dir: Path) -> str:
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    parts = text.split("---", 2) if text.startswith("---") else []
    data = yaml.safe_load(parts[1]) if len(parts) == 3 else {}
    return str((data or {}).get("name") or skill_dir.name)


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _append_workspace_rules(path: Path) -> bool:
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    if WORKSPACE_RULES in current:
        return False
    prefix = current.rstrip() + "\n\n" if current.strip() else "# AGENTS.md\n\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prefix + WORKSPACE_RULES.rstrip() + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return True


def migrate(
    root: Path,
    backup: Path | None,
    *,
    apply: bool,
    keep_local_profiles: set[str] | None = None,
) -> dict[str, object]:
    root = root.resolve()
    shared_root = root / "shared-skills"
    shared = _skill_roots(shared_root)
    if not shared:
        raise RuntimeError(f"no shared skills found under {shared_root}")
    shared_hashes = {name: _tree_hash(path) for name, path in shared.items()}
    shared_names = {relative: _skill_name(path) for relative, path in shared.items()}
    profiles = sorted(path for path in (root / "profiles").iterdir() if path.is_dir())
    keep_local_profiles = keep_local_profiles or set()
    plans: list[dict[str, object]] = []

    for profile in profiles:
        config_path = profile / "config.yaml"
        if not config_path.is_file() or config_path.is_symlink():
            raise RuntimeError(f"invalid Profile config: {config_path}")
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(config, dict):
            raise RuntimeError(f"Profile config is not an object: {config_path}")
        configured_external = ((config.get("skills") or {}).get("external_dirs") or [])
        if isinstance(configured_external, str):
            configured_external = [configured_external]
        already_external = str(shared_root) in [str(path) for path in configured_external]
        local = _skill_roots(profile / "skills")
        identical = sorted(
            name
            for name, path in local.items()
            if name in shared_hashes and _tree_hash(path) == shared_hashes[name]
        )
        differing = sorted(name for name in local if name in shared and name not in identical)
        private = sorted(set(local) - set(shared))
        missing = (
            []
            if already_external
            else sorted({shared_names[path] for path in set(shared) - set(local)})
        )
        plans.append(
            {
                "profile": profile,
                "config": config,
                "local": local,
                "identical": identical,
                "differing": differing,
                "private": private,
                "disabled": missing,
            }
        )

    summary: dict[str, object] = {
        "profiles": len(plans),
        "shared_skills": len(shared),
        "identical_copies": sum(len(plan["identical"]) for plan in plans),
        "differing_copies": sum(len(plan["differing"]) for plan in plans),
        "private_skills": sum(len(plan["private"]) for plan in plans),
        "disabled_additions": sum(len(plan["disabled"]) for plan in plans),
        "kept_local_profiles": sorted(keep_local_profiles),
        "applied": apply,
    }
    if not apply:
        return summary
    if backup is None:
        raise RuntimeError("--backup-dir is required with --apply")
    backup = backup.resolve()
    if backup.exists():
        raise RuntimeError(f"backup already exists: {backup}")
    backup.mkdir(parents=True, mode=0o700)

    for plan in plans:
        profile = plan["profile"]
        assert isinstance(profile, Path)
        config = plan["config"]
        assert isinstance(config, dict)
        profile_backup = backup / "profiles" / profile.name
        profile_backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(profile / "config.yaml", profile_backup / "config.yaml")
        agents_path = profile / "workspace" / "AGENTS.md"
        if agents_path.is_file():
            (profile_backup / "workspace").mkdir(parents=True, exist_ok=True)
            shutil.copy2(agents_path, profile_backup / "workspace" / "AGENTS.md")

        skills = config.get("skills")
        if not isinstance(skills, dict):
            skills = {}
            config["skills"] = skills
        external = skills.get("external_dirs")
        if isinstance(external, str):
            external = [external]
        elif not isinstance(external, list):
            external = []
        shared_path = str(shared_root)
        skills["external_dirs"] = [
            *[str(path) for path in external if str(path) != shared_path],
            shared_path,
        ]
        disabled = set(str(name) for name in (skills.get("disabled") or []))
        disabled.update(plan["disabled"])
        if disabled:
            skills["disabled"] = sorted(disabled)
        atomic_yaml_write(profile / "config.yaml", config, create_mode=0o600)
        _append_workspace_rules(agents_path)

        local = plan["local"]
        assert isinstance(local, dict)
        if profile.name in keep_local_profiles:
            continue
        for name in plan["identical"]:
            source = local[name]
            relative = source.relative_to(profile / "skills")
            destination = profile_backup / "skills" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
        skills_root = profile / "skills"
        for directory in sorted(
            (path for path in skills_root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass

    (backup / "migration-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/opt/data"))
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--keep-local-profile", action="append", default=[])
    args = parser.parse_args()
    print(
        json.dumps(
            migrate(
                args.root,
                args.backup_dir,
                apply=args.apply,
                keep_local_profiles=set(args.keep_local_profile),
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
