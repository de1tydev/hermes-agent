import importlib.util
import json
from pathlib import Path

import yaml


SCRIPT = Path(__file__).parents[2] / "scripts/migrate_tode68_openclaw_cron.py"
SPEC = importlib.util.spec_from_file_location("tode68_cron_migration", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _fixture(tmp_path: Path):
    legacy = tmp_path / "legacy"
    target = tmp_path / "target"
    (legacy / "cron").mkdir(parents=True)
    (target / "migration").mkdir(parents=True)
    profiles = []
    bindings = []
    for kind, identity, profile, agent in (
        ("dm", "ou_test", "feishu-dm-test", "agent-user"),
        ("group", "oc_test", "feishu-group-test", "agent-group"),
    ):
        profile_dir = target / "profiles" / profile
        (profile_dir / "workspace").mkdir(parents=True)
        (profile_dir / "cron").mkdir()
        (profile_dir / "config.yaml").write_text(
            "model:\n  default: deepseek-v4-flash\nplatforms:\n  feishu:\n"
            f"    home_channel:\n      platform: feishu\n      chat_id: {identity}\n      name: {profile}\n",
            encoding="utf-8",
        )
        digest = MODULE.identity_digest(kind, identity)
        profiles.append({
            "profile": profile,
            "kind": kind,
            "source_agent": agent,
            "identity_digest": f"sha256:{digest}",
        })
        bindings.append((kind, identity, profile))
    (target / "profiles/main/workspace").mkdir(parents=True)
    (target / "profiles/main/config.yaml").write_text("model: {}\n", encoding="utf-8")
    profiles.append({"profile": "main", "kind": "unbound", "source_agent": "main"})
    (target / "config.yaml").write_text("gateway:\n  multiplex_profiles: true\n", encoding="utf-8")
    (target / "migration/migration-manifest.json").write_text(
        json.dumps({"profiles": profiles}), encoding="utf-8"
    )
    jobs = [
        {
            "id": "daily",
            "name": "ai-daily-digest-9am",
            "enabled": True,
            "createdAtMs": 1700000000000,
            "schedule": {"kind": "cron", "expr": "0 9 * * *", "tz": "Asia/Shanghai"},
            "payload": {"kind": "agentTurn", "message": "使用 minimax__web_search 后用 message 发到 chat:oc_test"},
            "delivery": {"mode": "announce", "to": "feishu:oc_test"},
        },
        {
            "id": "once",
            "name": "water-reminder-test-user",
            "enabled": False,
            "createdAtMs": 1700000000000,
            "schedule": {"kind": "at", "at": "2026-03-12T06:20:00.000Z"},
            "payload": {"kind": "agentTurn", "message": "send user:ou_test"},
            "delivery": {"mode": "none"},
        },
        {
            "id": "memory",
            "name": "Memory Dreaming Promotion",
            "enabled": True,
            "createdAtMs": 1700000000000,
            "schedule": {"kind": "cron", "expr": "30 1 * * *"},
            "payload": {"kind": "agentTurn", "message": "__openclaw_memory_core_short_term_promotion_dream__"},
            "delivery": {"mode": "none"},
        },
    ]
    (legacy / "cron/jobs.json.migrated").write_text(
        json.dumps({"version": 1, "jobs": jobs}), encoding="utf-8"
    )
    (legacy / "cron/jobs-state.json.migrated").write_text(
        json.dumps({"version": 1, "jobs": {}}), encoding="utf-8"
    )
    return legacy, target


def test_plan_maps_profiles_adapts_prompts_and_quarantines_internal_jobs(tmp_path):
    legacy, target = _fixture(tmp_path)
    plan = MODULE.build_plan(legacy, target)
    assert plan["summary"] == {
        "imported": 2,
        "enabled": 1,
        "disabled": 1,
        "review": 1,
        "profiles": 2,
    }
    digest = next(row for row in plan["imports"] if row["job"]["id"] == "oc-daily")
    assert digest["profile"] == "feishu-group-test"
    assert "minimax__web_search" not in digest["job"]["prompt"]
    assert digest["job"]["deliver"] == "feishu"
    once = next(row for row in plan["imports"] if row["job"]["id"] == "oc-once")
    assert once["job"]["enabled"] is False
    assert once["job"]["next_run_at"] is None
    assert once["job"]["deliver"] == "local"


def test_apply_is_idempotent_and_sets_timezone(tmp_path):
    legacy, target = _fixture(tmp_path)
    plan = MODULE.build_plan(legacy, target)
    first = MODULE.apply_plan(plan, legacy, target)
    second = MODULE.apply_plan(plan, legacy, target)
    assert first["jobs_added"] == 2
    assert second["jobs_added"] == 0
    assert second["jobs_unchanged"] == 2
    for profile in ("feishu-dm-test", "feishu-group-test"):
        config = yaml.safe_load((target / "profiles" / profile / "config.yaml").read_text())
        assert config["timezone"] == "Asia/Shanghai"
    jobs = json.loads(
        (target / "profiles/feishu-group-test/cron/jobs.json").read_text()
    )["jobs"]
    assert [job["id"] for job in jobs] == ["oc-daily"]
    manifest = json.loads(
        (target / "migration/openclaw-cron-migration-manifest.json").read_text()
    )
    assert "prompt" not in manifest["imports"][0]["job"]
    assert (target / "migration/openclaw-cron-source/jobs.json.migrated").is_file()
