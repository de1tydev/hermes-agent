#!/usr/bin/env python3
"""Validate a TODE68 Hermes home without connecting Feishu ingress."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import urllib.request

import yaml


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_raw_registry(source: Path, relative: str) -> dict[str, str]:
    value = json.loads((source / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(agent, str) for key, agent in value.items()
    ):
        raise RuntimeError(f"invalid source registry: {relative}")
    return value


def source_for(kind: str, identity: str):
    from gateway.session import Platform

    if kind == "dm":
        return SimpleNamespace(
            platform=Platform.FEISHU,
            chat_type="dm",
            user_id=identity,
            chat_id="synthetic-dm-container",
        )
    return SimpleNamespace(
        platform=Platform.FEISHU,
        chat_type="group",
        user_id="synthetic-member",
        chat_id=identity,
    )


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        values[name] = value
    return values


def probe_credentials(target: Path) -> dict[str, bool]:
    from hermes_cli.env_loader import load_hermes_dotenv
    from plugins.platforms.feishu.adapter import probe_bot

    primary = read_env(target / ".env")
    secondary = read_env(target / "gateway-secondary.env")
    request = urllib.request.Request(
        "https://newapi.tode.ltd/v1/models",
        headers={"Authorization": f"Bearer {primary['OPENAI_API_KEY']}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        provider_ok = response.status == 200
    primary_bot = probe_bot(
        primary["FEISHU_APP_ID"], primary["FEISHU_APP_SECRET"], "feishu"
    )
    secondary_bot = probe_bot(
        secondary["FEISHU_APP_ID"], secondary["FEISHU_APP_SECRET"], "feishu"
    )
    os.environ["HERMES_ENV_OVERLAY"] = str(target / "gateway-secondary.env")
    load_hermes_dotenv(hermes_home=target, load_external_secrets=False)
    secondary_overlay_ok = (
        os.environ.get("FEISHU_APP_ID") == secondary["FEISHU_APP_ID"]
        and os.environ.get("FEISHU_APP_SECRET") == secondary["FEISHU_APP_SECRET"]
    )
    return {
        "feishu_primary": bool(primary_bot),
        "feishu_secondary": bool(secondary_bot),
        "gateway_secondary_overlay": secondary_overlay_ok,
        "provider": provider_ok,
    }


def verify_source_snapshot(
    source: Path, target: Path, manifest: dict
) -> dict[str, int | bool]:
    cache: dict[Path, str] = {}
    checked = 0
    drifted = 0

    def check(path: Path, expected: str) -> None:
        nonlocal checked, drifted
        checked += 1
        if path.is_symlink() or not path.is_file():
            drifted += 1
            return
        actual = cache.get(path)
        if actual is None:
            actual = sha256_file(path)
            cache[path] = actual
        if actual != expected:
            drifted += 1

    for relative, row in manifest["source_snapshot"].items():
        check(source / relative, row["sha256"])
    for profile_row in manifest["profiles"]:
        workspace = source / profile_row["source_workspace"]
        for document in profile_row["document_records"]:
            if document["action"] in {"copy", "review"} and "sha256" in document:
                check(workspace / document["path"], document["sha256"])
        archive_index = json.loads(
            (
                target
                / "profiles"
                / profile_row["profile"]
                / "legacy-memory/index.json"
            ).read_text(encoding="utf-8")
        )
        for memory_object in archive_index["objects"]:
            for relative in memory_object["paths"]:
                check(workspace / relative, memory_object["sha256"])
    return {
        "checked_refs": checked,
        "drifted_refs": drifted,
        "passed": drifted == 0,
        "unique_files_hashed": len(cache),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--exercise-auto-provision", action="store_true")
    parser.add_argument("--probe-credentials", action="store_true")
    parser.add_argument("--verify-source-snapshot", action="store_true")
    args = parser.parse_args()

    source = args.source.resolve(strict=True)
    target = args.target.resolve(strict=True)
    os.environ["HERMES_HOME"] = str(target)

    manifest = json.loads(
        (target / "migration/migration-manifest.json").read_text(encoding="utf-8")
    )
    oracle = json.loads(
        (target / "migration/external-memory-disabled.json").read_text(
            encoding="utf-8"
        )
    )
    registry_data = json.loads(
        (target / "state/profile-identity-registry.json").read_text(encoding="utf-8")
    )
    assert oracle["passed"] is True
    assert manifest["external_memory"] == "disabled"
    assert len(manifest["profiles"]) == 43
    assert len(registry_data["bindings"]) == 39
    assert importlib.util.find_spec("hindsight") is None

    from gateway.config import load_gateway_config
    from gateway.profile_provisioning import ProfileIdentityRegistry
    from gateway.session import Platform
    from hermes_cli.profiles import profiles_to_serve

    config = load_gateway_config()
    assert config.multiplex_profiles is True
    assert config.group_sessions_per_user is False
    assert config.platforms[Platform.FEISHU].extra["profile_auto_provision"] is True
    assert len(profiles_to_serve(multiplex=True)) == 44

    registry = ProfileIdentityRegistry(target)
    users = load_raw_registry(
        source, "automation/auto-user-isolation/users.json"
    )
    groups = load_raw_registry(
        source, "automation/auto-group-isolation/groups.json"
    )
    dm_identity = next(iter(sorted(users))).removeprefix("feishu:")
    group_identity = next(iter(sorted(groups))).removeprefix("feishu:")
    dm_source = source_for("dm", dm_identity)
    group_source = source_for("group", group_identity)
    assert registry.lookup(dm_source) == registry.deterministic_profile_name(dm_source)
    assert registry.lookup(group_source) == registry.deterministic_profile_name(group_source)
    assert registry.lookup(dm_source) != registry.lookup(group_source)

    by_agent: dict[str, list[str]] = {}
    for row in manifest["profiles"]:
        by_agent.setdefault(row["source_agent"], []).append(row["profile"])
        profile = target / "profiles" / row["profile"]
        assert len((profile / "memories/MEMORY.md").read_text()) <= 20_000
        assert len((profile / "memories/USER.md").read_text()) <= 4_000
        assert "FEISHU_" not in (profile / ".env").read_text()
        config_text = (profile / "config.yaml").read_text().casefold()
        assert not any(
            provider in config_text
            for provider in ("hindsight", "honcho", "mem0", "openviking")
        )
    shared = next(profiles for profiles in by_agent.values() if len(profiles) > 1)
    memory_paths = [
        target / "profiles" / profile / "memories/MEMORY.md" for profile in shared
    ]
    assert len({path.stat().st_ino for path in memory_paths}) == len(memory_paths)

    created_profile = None
    if args.exercise_auto_provision:
        unknown = source_for("dm", "ou_synthetic_auto_provision_canary")
        created_profile = registry.resolve_or_provision(
            unknown, static_profile=None, profile_allowlist=None
        )
        profile = target / "profiles" / created_profile
        assert (profile / ".hermes-auto-profile.json").is_file()
        assert "OPENAI_API_KEY=" in (profile / ".env").read_text()
        assert "FEISHU_" not in (profile / ".env").read_text()
        config_data = yaml.safe_load((profile / "config.yaml").read_text())
        assert "gateway" not in config_data
        assert config_data["terminal"]["cwd"] == str(profile / "workspace")
        assert (profile / "skills").is_dir()

    credential_probes = (
        probe_credentials(target)
        if args.probe_credentials
        else {
            "feishu_primary": False,
            "feishu_secondary": False,
            "gateway_secondary_overlay": False,
            "provider": False,
        }
    )
    if args.probe_credentials:
        assert all(credential_probes.values())
    source_verification = (
        verify_source_snapshot(source, target, manifest)
        if args.verify_source_snapshot
        else {
            "checked_refs": 0,
            "drifted_refs": 0,
            "passed": False,
            "unique_files_hashed": 0,
        }
    )
    if args.verify_source_snapshot and not source_verification["passed"]:
        print(
            json.dumps(
                {"source_verification": source_verification, "status": "source_drift"},
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "auto_profile_created": created_profile is not None,
                "external_memory_disabled": True,
                "credential_probes": credential_probes,
                "identity_routes": len(registry_data["bindings"]),
                "profiles": len(manifest["profiles"]),
                "served_profiles_including_default": 44,
                "source_verification": source_verification,
                "status": "passed",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
