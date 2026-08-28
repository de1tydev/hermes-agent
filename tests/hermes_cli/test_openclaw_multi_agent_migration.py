"""Synthetic coverage for preview-first OpenClaw multi-agent migration."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_cli.openclaw_multi_agent_migration import (
    MultiAgentMigration,
    MultiAgentMigrationError,
    canonical_manifest_bytes,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def build_source(root: Path) -> Path:
    agents = []
    bindings = []
    users = []
    groups = []

    for index in range(25):
        agent_id = f"dm-agent-{index:02d}"
        identity = f"synthetic-user-{index:02d}"
        workspace = f"workspaces/{agent_id}"
        agents.append({"id": agent_id, "workspace": workspace})
        users.append({"agent_id": agent_id, "identity": identity})
        bindings.append(
            {
                "agentId": agent_id,
                "match": {
                    "channel": "feishu",
                    "peer": {"kind": "direct", "id": identity},
                },
            }
        )
        (root / workspace).mkdir(parents=True)
        (root / workspace / "AGENTS.md").write_text("synthetic dm\n", encoding="utf-8")

    for index in range(14):
        agent_id = f"group-agent-{index:02d}"
        identity = f"synthetic-group-{index:02d}"
        workspace = f"workspaces/{agent_id}"
        agents.append({"id": agent_id, "workspace": workspace})
        groups.append({"agent_id": agent_id, "identity": identity})
        bindings.append(
            {
                "agentId": agent_id,
                "match": {
                    "channel": "feishu",
                    "peer": {"kind": "group", "id": identity},
                },
            }
        )
        (root / workspace).mkdir(parents=True)
        (root / workspace / "AGENTS.md").write_text(
            "synthetic group\n", encoding="utf-8"
        )

    for agent_id in ("main", "power-knowledge"):
        workspace = f"workspaces/{agent_id}"
        agents.append({"id": agent_id, "workspace": workspace})
        (root / workspace).mkdir(parents=True)
        (root / workspace / "AGENTS.md").write_text(
            "synthetic functional\n", encoding="utf-8"
        )

    bindings.append({"agentId": "main", "match": {"channel": "feishu"}})
    _write_json(root / "openclaw.json", {"agents": {"list": agents}, "bindings": bindings})
    _write_json(
        root / "automation/users.json",
        {"schema_version": "openclaw-automation-user-registry/v1", "entries": users},
    )
    _write_json(
        root / "automation/groups.json",
        {"schema_version": "openclaw-automation-group-registry/v1", "entries": groups},
    )
    return root


def test_preview_reconciles_exact_inventory_and_is_byte_stable(tmp_path):
    source = build_source(tmp_path / "source")
    migration = MultiAgentMigration(source, tmp_path / "target")

    first = migration.preview()
    second = migration.preview()

    assert first["schema_version"] == "hermes-openclaw-multi-agent-manifest/v1"
    assert first["counts"] == {
        "bindings": 40,
        "functional_profiles": 2,
        "group_profiles": 14,
        "profiles": 41,
        "review_bindings": 1,
        "user_profiles": 25,
    }
    assert len(first["profile_records"]) == 41
    assert len(first["binding_records"]) == 40
    assert canonical_manifest_bytes(first) == canonical_manifest_bytes(second)
    assert not (tmp_path / "target").exists()


def test_tracked_fixture_matches_frozen_inventory(tmp_path):
    source = Path(__file__).parents[1] / "fixtures/openclaw-multi-agent"
    manifest = MultiAgentMigration(source, tmp_path / "target").preview()

    assert manifest["counts"] == {
        "bindings": 40,
        "functional_profiles": 2,
        "group_profiles": 14,
        "profiles": 41,
        "review_bindings": 1,
        "user_profiles": 25,
    }
    assert not (tmp_path / "target").exists()


def test_manifest_uses_registry_v1_digests_not_raw_identities(tmp_path):
    source = build_source(tmp_path / "source")
    manifest = MultiAgentMigration(source, tmp_path / "target").preview()
    payload = canonical_manifest_bytes(manifest).decode("utf-8")

    assert "synthetic-user-00" not in payload
    assert "synthetic-group-00" not in payload
    materialized = [
        record for record in manifest["binding_records"] if record["action"] == "materialize"
    ]
    assert len(materialized) == 39
    assert all(record["registry_key"].startswith("feishu:") for record in materialized)
    assert all(record["identity_digest"].startswith("sha256:") for record in materialized)
    assert manifest["binding_records"][-1]["action"] == "review"


def test_denylist_and_secret_content_never_enter_payload(tmp_path):
    source = build_source(tmp_path / "source")
    workspace = source / "workspaces/dm-agent-00"
    (workspace / "sessions").mkdir()
    (workspace / "sessions/chat.jsonl").write_text("private conversation", encoding="utf-8")
    (workspace / "auth-profiles.json").write_text(
        '{"token":"synthetic-secret-value"}', encoding="utf-8"
    )
    (workspace / "notes.md").write_text("api_key=sk-synthetic-canary", encoding="utf-8")
    for dirname in (
        "build",
        "cache",
        "checkpoints",
        "deleted",
        "logs",
        "media",
        "resets",
        "trajectory",
    ):
        denied = workspace / dirname
        denied.mkdir()
        (denied / "payload.bin").write_bytes(b"excluded")
    (workspace / "state.db").write_bytes(b"sqlite index")
    (workspace / ".env.local").write_text("TOKEN=excluded", encoding="utf-8")

    manifest = MultiAgentMigration(source, tmp_path / "target").preview()
    payload = canonical_manifest_bytes(manifest).decode("utf-8")
    refs = {ref["relative_path"]: ref for ref in manifest["classified_source_refs"]}

    assert refs["workspaces/dm-agent-00/sessions"]["action"] == "exclude"
    assert refs["workspaces/dm-agent-00/auth-profiles.json"]["action"] == "exclude"
    assert refs["workspaces/dm-agent-00/notes.md"]["action"] == "exclude"
    for relative in (
        ".env.local",
        "build",
        "cache",
        "checkpoints",
        "deleted",
        "logs",
        "media",
        "resets",
        "state.db",
        "trajectory",
    ):
        assert refs[f"workspaces/dm-agent-00/{relative}"]["action"] == "exclude"
    assert "private conversation" not in payload
    assert "synthetic-secret-value" not in payload
    assert "sk-synthetic-canary" not in payload


def test_symlink_is_reviewed_and_never_followed(tmp_path):
    source = build_source(tmp_path / "source")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("do-not-read", encoding="utf-8")
    link = source / "workspaces/dm-agent-00/external"
    link.symlink_to(outside)

    manifest = MultiAgentMigration(source, tmp_path / "target").preview()
    ref = next(
        item for item in manifest["classified_source_refs"] if item["relative_path"].endswith("/external")
    )

    assert ref == {
        "action": "review",
        "classification": "symlink",
        "relative_path": "workspaces/dm-agent-00/external",
    }
    assert "do-not-read" not in canonical_manifest_bytes(manifest).decode("utf-8")


def test_missing_workspace_fails_before_target_write(tmp_path):
    source = build_source(tmp_path / "source")
    config_path = source / "openclaw.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["agents"]["list"][0]["workspace"] = "workspaces/missing"
    _write_json(config_path, config)

    with pytest.raises(MultiAgentMigrationError, match="workspace_missing"):
        MultiAgentMigration(source, tmp_path / "target").preview()
    assert not (tmp_path / "target").exists()


def test_conflicting_duplicate_binding_fails_closed(tmp_path):
    source = build_source(tmp_path / "source")
    config_path = source / "openclaw.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    duplicate = json.loads(json.dumps(config["bindings"][0]))
    duplicate["agentId"] = "dm-agent-01"
    config["bindings"].append(duplicate)
    _write_json(config_path, config)

    with pytest.raises(MultiAgentMigrationError, match="binding_conflict"):
        MultiAgentMigration(source, tmp_path / "target").preview()


def test_preview_enumerates_target_binding_conflict_and_apply_refuses(tmp_path):
    source = build_source(tmp_path / "source")
    target = tmp_path / "target"
    initial = MultiAgentMigration(source, target).preview()
    first = next(
        record for record in initial["binding_records"] if record["action"] == "materialize"
    )
    _write_json(
        target / "state/profile-identity-registry.json",
        {
            "schema_version": "hermes-profile-identity-registry/v1",
            "bindings": {
                first["registry_key"]: {
                    "identity_digest": first["identity_digest"],
                    "kind": first["kind"],
                    "platform": "feishu",
                    "profile": "conflicting-profile",
                }
            },
        },
    )

    migration = MultiAgentMigration(source, target)
    manifest = migration.preview()

    assert manifest["conflicts"] == [
        {
            "code": "target_binding_conflict",
            "record_id": first["record_id"],
            "target": "state/profile-identity-registry.json",
        }
    ]
    with pytest.raises(MultiAgentMigrationError, match="target_conflict"):
        migration.apply(manifest)
    assert not (target / "profiles").exists()


def test_apply_rejects_source_drift_without_writes(tmp_path):
    source = build_source(tmp_path / "source")
    target = tmp_path / "target"
    migration = MultiAgentMigration(source, target)
    manifest = migration.preview()
    (source / "workspaces/main/AGENTS.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(MultiAgentMigrationError, match="source_drift"):
        migration.apply(manifest)
    assert not target.exists()


def test_apply_materializes_profiles_registry_restore_point_and_is_idempotent(tmp_path):
    source = build_source(tmp_path / "source")
    target = tmp_path / "target"
    migration = MultiAgentMigration(source, target)
    manifest = migration.preview()

    first = migration.apply(manifest)
    second = migration.apply(manifest)

    assert first["status"] == "applied"
    assert second["status"] == "unchanged"
    registry = json.loads(
        (target / "state/profile-identity-registry.json").read_text(encoding="utf-8")
    )
    assert registry["schema_version"] == "hermes-profile-identity-registry/v1"
    assert len(registry["bindings"]) == 39
    for profile in manifest["profile_records"]:
        profile_root = target / profile["target_profile_root"]
        assert (profile_root / "workspace").is_dir()
        assert (profile_root / ".hermes-openclaw-profile.json").is_file()
    assert Path(first["restore_point"]).is_dir()
    restore_receipt = json.loads(
        (Path(first["restore_point"]) / "receipt.json").read_text(encoding="utf-8")
    )
    assert len(restore_receipt["created_profile_roots"]) == 41
    assert restore_receipt["registry_target"] == "state/profile-identity-registry.json"


def test_apply_rolls_back_all_target_changes_on_failure(tmp_path, monkeypatch):
    source = build_source(tmp_path / "source")
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "sentinel.txt"
    sentinel.write_text("before\n", encoding="utf-8")
    migration = MultiAgentMigration(source, target)
    manifest = migration.preview()

    def fail_after_first_write(*_args, **_kwargs):
        raise OSError("synthetic failure")

    monkeypatch.setattr(migration, "_publish_manifest", fail_after_first_write)
    with pytest.raises(MultiAgentMigrationError, match="apply_failed"):
        migration.apply(manifest)

    assert sentinel.read_text(encoding="utf-8") == "before\n"
    assert not (target / "profiles").exists()
    assert not (target / "state/profile-identity-registry.json").exists()


def test_cli_dry_run_is_zero_write_and_apply_requires_reviewed_manifest(tmp_path):
    source = build_source(tmp_path / "source")
    target = tmp_path / "target"
    script = (
        Path(__file__).parents[2]
        / "optional-skills/migration/openclaw-migration/scripts/openclaw_to_hermes.py"
    )

    preview = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source",
            str(source),
            "--target",
            str(target),
            "--dry-run",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    manifest = json.loads(preview.stdout)
    assert manifest["counts"]["profiles"] == 41
    assert not target.exists()

    rejected = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source",
            str(source),
            "--target",
            str(target),
            "--multi-agent",
            "--execute",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert "manifest-input" in rejected.stdout

    manifest_path = tmp_path / "reviewed-manifest.json"
    manifest_path.write_bytes(canonical_manifest_bytes(manifest))
    applied = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source",
            str(source),
            "--target",
            str(target),
            "--multi-agent",
            "--execute",
            "--manifest-input",
            str(manifest_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(applied.stdout)["status"] == "applied"
