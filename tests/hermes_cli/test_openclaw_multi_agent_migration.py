"""Synthetic coverage for preview-first OpenClaw multi-agent migration."""

from __future__ import annotations

import json
import os
import shutil
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


def resolved_migration(source: Path, target: Path) -> MultiAgentMigration:
    unresolved = MultiAgentMigration(source, target).preview()
    review = next(
        record for record in unresolved["binding_records"] if record["action"] == "review"
    )
    resolutions = {
        "schema_version": "hermes-openclaw-binding-resolutions/v1",
        "resolutions": [
            {
                "outcome": "retire",
                "reason_code": "channel_wide_route_replaced_by_identity_routes",
                "record_id": review["record_id"],
                "source_binding_sha256": review["source_binding_sha256"],
            }
        ],
    }
    return MultiAgentMigration(source, target, binding_resolutions=resolutions)


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
        "materialized_bindings": 39,
        "profiles": 41,
        "resolved_bindings": 0,
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
        "materialized_bindings": 39,
        "profiles": 41,
        "resolved_bindings": 0,
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


@pytest.mark.parametrize(
    "payload",
    [
        b'{"botToken":"synthetic-token-value"}',
        b'{"authorization":"Bearer synthetic-token-value"}',
    ],
)
def test_common_secret_spellings_are_excluded(tmp_path, payload):
    source = build_source(tmp_path / "source")
    note = source / "workspaces/dm-agent-00/operator-notes.json"
    note.write_bytes(payload)

    manifest = MultiAgentMigration(source, tmp_path / "target").preview()
    ref = next(
        item
        for item in manifest["classified_source_refs"]
        if item["relative_path"].endswith("operator-notes.json")
    )

    assert ref["classification"] == "secret-content"
    assert ref["action"] == "exclude"
    assert "synthetic-token-value" not in canonical_manifest_bytes(manifest).decode()


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


def test_apply_refuses_unresolved_binding_without_target_writes(tmp_path):
    source = build_source(tmp_path / "source")
    target = tmp_path / "target"
    migration = MultiAgentMigration(source, target)
    manifest = migration.preview()

    with pytest.raises(MultiAgentMigrationError, match="binding_unresolved"):
        migration.apply(manifest)
    assert not target.exists()


def test_canonical_binding_resolution_closes_all_40_outcomes(tmp_path):
    source = build_source(tmp_path / "source")
    target = tmp_path / "target"
    migration = resolved_migration(source, target)
    manifest = migration.preview()

    assert manifest["counts"]["bindings"] == 40
    assert manifest["counts"]["materialized_bindings"] == 39
    assert manifest["counts"]["resolved_bindings"] == 1
    assert manifest["counts"]["review_bindings"] == 0
    assert len(manifest["binding_records"]) == 40
    resolved = next(
        record for record in manifest["binding_records"] if record["action"] == "resolved"
    )
    assert resolved["outcome"] == "retire"

    result = migration.apply(manifest)

    assert result["status"] == "applied"
    assert result["binding_outcomes"] == {
        "materialized": 39,
        "resolved": 1,
    }


def test_apply_materializes_profiles_registry_restore_point_and_is_idempotent(tmp_path):
    source = build_source(tmp_path / "source")
    target = tmp_path / "target"
    migration = resolved_migration(source, target)
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
    migration = resolved_migration(source, target)
    manifest = migration.preview()

    def fail_after_first_write(*_args, **_kwargs):
        raise OSError("synthetic failure")

    monkeypatch.setattr(migration, "_publish_manifest", fail_after_first_write)
    with pytest.raises(MultiAgentMigrationError, match="apply_failed"):
        migration.apply(manifest)

    assert sentinel.read_text(encoding="utf-8") == "before\n"
    assert not (target / "profiles").exists()
    assert not (target / "state/profile-identity-registry.json").exists()


def test_manifest_binding_tamper_is_rejected_before_writes(tmp_path):
    source = build_source(tmp_path / "source")
    target = tmp_path / "target"
    migration = resolved_migration(source, target)
    manifest = migration.preview()
    materialized = next(
        record for record in manifest["binding_records"] if record["action"] == "materialize"
    )
    materialized["profile"] = "main"

    with pytest.raises(MultiAgentMigrationError, match="manifest_mismatch"):
        migration.apply(manifest)
    assert not target.exists()


@pytest.mark.parametrize("mutation", ["delete", "insert"])
def test_manifest_binding_cardinality_tamper_is_rejected(tmp_path, mutation):
    source = build_source(tmp_path / "source")
    target = tmp_path / "target"
    migration = resolved_migration(source, target)
    manifest = migration.preview()
    if mutation == "delete":
        manifest["binding_records"].pop()
    else:
        manifest["binding_records"].append(dict(manifest["binding_records"][0]))

    with pytest.raises(MultiAgentMigrationError, match="manifest_invalid"):
        migration.apply(manifest)
    assert not target.exists()


@pytest.mark.parametrize("escape", ["../escaped-profile", "/tmp/escaped-profile"])
def test_manifest_target_escape_is_rejected_before_writes(tmp_path, escape):
    source = build_source(tmp_path / "source")
    target = tmp_path / "target"
    migration = resolved_migration(source, target)
    manifest = migration.preview()
    manifest["profile_records"][0]["target_profile_root"] = escape

    with pytest.raises(MultiAgentMigrationError, match="manifest_invalid"):
        migration.apply(manifest)
    assert not target.exists()
    assert not (tmp_path / "escaped-profile").exists()


@pytest.mark.parametrize("ancestor", ["profiles", "state", "migration"])
def test_target_ancestor_symlink_is_rejected_without_external_write(tmp_path, ancestor):
    source = build_source(tmp_path / "source")
    target = tmp_path / "target"
    outside = tmp_path / f"outside-{ancestor}"
    target.mkdir()
    outside.mkdir()
    (target / ancestor).symlink_to(outside, target_is_directory=True)
    migration = resolved_migration(source, target)
    manifest = migration.preview()

    assert any(conflict["code"] == "target_ancestor_symlink" for conflict in manifest["conflicts"])
    with pytest.raises(MultiAgentMigrationError, match="target_conflict"):
        migration.apply(manifest)
    assert list(outside.iterdir()) == []


def test_existing_profile_without_workspace_marker_is_rejected(tmp_path):
    source = build_source(tmp_path / "source")
    owned_target = tmp_path / "owned-target"
    owned = resolved_migration(source, owned_target)
    owned_manifest = owned.preview()
    owned.apply(owned_manifest)
    profile = owned_manifest["profile_records"][0]

    target = tmp_path / "target"
    profile_root = target / profile["target_profile_root"]
    profile_root.parent.mkdir(parents=True)
    shutil.copytree(owned_target / profile["target_profile_root"], profile_root)
    (profile_root / "workspace/.hermes-openclaw-workspace.json").unlink()
    migration = resolved_migration(source, target)
    manifest = migration.preview()

    assert any(conflict["code"] == "target_profile_conflict" for conflict in manifest["conflicts"])
    with pytest.raises(MultiAgentMigrationError, match="target_conflict"):
        migration.apply(manifest)
    assert not (target / "state/profile-identity-registry.json").exists()


def test_fake_incomplete_restore_point_is_rejected_before_writes(tmp_path):
    source = build_source(tmp_path / "source")
    target = tmp_path / "target"
    migration = resolved_migration(source, target)
    manifest = migration.preview()
    snapshot = manifest["source_snapshot"]["id"].removeprefix("sha256:")
    restore = target / "migration/openclaw/restore-points" / snapshot
    restore.mkdir(parents=True)
    (restore / "unowned.txt").write_text("collision", encoding="utf-8")

    manifest = migration.preview()

    assert any(conflict["code"] == "restore_point_invalid" for conflict in manifest["conflicts"])
    with pytest.raises(MultiAgentMigrationError, match="target_conflict"):
        migration.apply(manifest)
    assert not (target / "profiles").exists()
    assert not (target / "state/profile-identity-registry.json").exists()


def test_same_size_large_file_replacement_changes_snapshot_and_rejects_apply(tmp_path):
    source = build_source(tmp_path / "source")
    large = source / "workspaces/main/review.bin"
    large.write_bytes(b"A" * (1024 * 1024 + 1))
    target = tmp_path / "target"
    migration = resolved_migration(source, target)
    manifest = migration.preview()
    original_stat = large.stat()
    large.write_bytes(b"B" * original_stat.st_size)
    os.utime(large, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    replacement = migration.preview()
    ref = next(
        item for item in replacement["classified_source_refs"] if item["relative_path"].endswith("review.bin")
    )

    assert ref["classification"] == "large-file"
    assert "sha256" in ref and "mtime_ns" in ref
    assert replacement["source_snapshot"]["id"] != manifest["source_snapshot"]["id"]
    with pytest.raises(MultiAgentMigrationError, match="source_drift"):
        migration.apply(manifest)
    assert not target.exists()


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

    review = next(
        record for record in manifest["binding_records"] if record["action"] == "review"
    )
    resolutions = {
        "schema_version": "hermes-openclaw-binding-resolutions/v1",
        "resolutions": [
            {
                "outcome": "retire",
                "reason_code": "channel_wide_route_replaced_by_identity_routes",
                "record_id": review["record_id"],
                "source_binding_sha256": review["source_binding_sha256"],
            }
        ],
    }
    resolution_path = tmp_path / "binding-resolutions.json"
    resolution_path.write_text(json.dumps(resolutions), encoding="utf-8")
    resolved_preview = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source",
            str(source),
            "--target",
            str(target),
            "--multi-agent",
            "--dry-run",
            "--binding-resolutions",
            str(resolution_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    resolved_manifest = json.loads(resolved_preview.stdout)
    assert resolved_manifest["counts"]["review_bindings"] == 0
    assert resolved_manifest["counts"]["resolved_bindings"] == 1

    manifest_path = tmp_path / "reviewed-manifest.json"
    manifest_path.write_bytes(canonical_manifest_bytes(resolved_manifest))
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
            "--binding-resolutions",
            str(resolution_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(applied.stdout)["status"] == "applied"
