from __future__ import annotations

import json
from pathlib import Path

from scripts.migrate_tode68_openclaw import inventory, run


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _source(root: Path) -> Path:
    agents = []
    for name in ("shared-agent", "orphan-agent", "main"):
        workspace = root / f"workspace-{name}"
        workspace.mkdir(parents=True)
        (workspace / "MEMORY.md").write_text(f"memory for {name}\n", encoding="utf-8")
        (workspace / "USER.md").write_text(f"user for {name}\n", encoding="utf-8")
        (workspace / "AGENTS.md").write_text(f"instructions for {name}\n", encoding="utf-8")
        (workspace / "sessions").mkdir()
        (workspace / "sessions/chat.jsonl").write_text("chat history", encoding="utf-8")
        agents.append({"id": name, "workspace": str(workspace)})
    (root / "skills/sample").mkdir(parents=True)
    (root / "skills/sample/SKILL.md").write_text("# Sample\n", encoding="utf-8")
    users = {
        "feishu:ou_alpha": "shared-agent",
        "feishu:ou_beta": "shared-agent",
    }
    groups = {"feishu:oc_group": "shared-agent"}
    bindings = [
        {
            "agentId": "shared-agent",
            "match": {"channel": "feishu", "peer": {"kind": "direct", "id": "ou_alpha"}},
        },
        {
            "agentId": "shared-agent",
            "match": {"channel": "feishu", "peer": {"kind": "direct", "id": "ou_beta"}},
        },
        {
            "agentId": "shared-agent",
            "match": {"channel": "feishu", "peer": {"kind": "group", "id": "oc_group"}},
        },
        {
            "agentId": "orphan-agent",
            "match": {"channel": "feishu", "peer": {"kind": "direct", "id": "ou_alpha"}},
        },
    ]
    _json(root / "automation/auto-user-isolation/users.json", users)
    _json(root / "automation/auto-group-isolation/groups.json", groups)
    _json(
        root / "openclaw.json",
        {
            "agents": {"list": agents},
            "bindings": bindings,
            "models": {"providers": {"tode": {"apiKey": "synthetic-provider-key"}}},
            "channels": {
                "feishu": {
                    "appId": "synthetic-primary-id",
                    "appSecret": "synthetic-primary-secret",
                    "accounts": {
                        "dingjiayu": {
                            "appId": "synthetic-secondary-id",
                            "appSecret": "synthetic-secondary-secret",
                        }
                    },
                }
            },
        },
    )
    return root


def test_inventory_splits_shared_chat_agents_and_preserves_unbound(tmp_path):
    profiles, facts = inventory(_source(tmp_path / "source"))

    assert facts["identity_profile_count"] == 3
    assert facts["profile_count"] == 5
    assert facts["unbound_profile_count"] == 2
    assert facts["duplicate_binding_rows"] == 1
    assert facts["mismatched_binding_rows"] == 1
    assert len({row.profile for row in profiles}) == 5


def test_apply_migrates_builtin_memory_assets_and_no_transport_secret(tmp_path):
    source = _source(tmp_path / "source")
    target = tmp_path / "hermes"

    result = run(source, target)

    assert result == {
        "identity_routes": 3,
        "profiles": 5,
        "status": "applied",
        "target": str(target),
        "unbound_profiles": 2,
    }
    registry = json.loads((target / "state/profile-identity-registry.json").read_text())
    assert len(registry["bindings"]) == 3
    manifest = json.loads((target / "migration/migration-manifest.json").read_text())
    assert manifest["external_memory"] == "disabled"
    assert len(manifest["profiles"]) == 5
    oracle = json.loads((target / "migration/external-memory-disabled.json").read_text())
    assert oracle["passed"] is True
    for row in manifest["profiles"]:
        profile = target / "profiles" / row["profile"]
        assert "FEISHU_" not in (profile / ".env").read_text()
        assert "synthetic-provider-key" in (profile / ".env").read_text()
        assert (profile / "memories/MEMORY.md").stat().st_size > 0
        assert (profile / "legacy-memory/index.json").is_file()
        assert (profile / "workspace/AGENTS.md").is_file()
        assert not (profile / "workspace/sessions/chat.jsonl").exists()
        assert (profile / "skills/sample/SKILL.md").is_file()
        assert (profile / "skills/legacy-memory-search/scripts/search.py").is_file()
