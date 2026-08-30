from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.optimize_tode68_profile_context import (
    MEMORY_LIMIT,
    USER_LIMIT,
    apply_plan,
    build_plan,
    curate_memory,
    render_agents,
    render_soul,
    restore_backup,
)


def _profile(root: Path, name: str, *, group: bool = False) -> Path:
    profile = root / "profiles" / name
    (profile / "memories").mkdir(parents=True)
    (profile / "workspace").mkdir()
    (profile / "skills/legacy-memory-search").mkdir(parents=True)
    (profile / "memories/MEMORY.md").write_text(
        "# MEMORY.md - 长期记忆\n\n"
        "- 长期偏好：回答要严谨。\n\n"
        "## Promoted From Short-Term Memory (2026-08-01)\n\n"
        "- Conversation Summary: 临时输出。\n\n"
        "# Deep Sleep\n\n- nothing\n",
        encoding="utf-8",
    )
    (profile / "memories/USER.md").write_text(
        "# USER.md\n\n- 专业领域：电力系统。\n\n"
        "The more you know, the better you can help.\n",
        encoding="utf-8",
    )
    (profile / "SOUL.md").write_text(
        "# SOUL.md\n\n### Background Tasks Must Use Sub Agents\n"
        "Use sessions_spawn.\n\n# IDENTITY.md\n\n"
        "- **Name:** TODEClaw-测试\n- **Vibe:** 专业、直接\n",
        encoding="utf-8",
    )
    agents = (
        "# AGENTS.md\n\n## Session Startup\nRead workspace/memory every time.\n"
        "Use @openclaw/feishu and sessions_spawn.\n"
    )
    if group:
        agents += "\n## Group Chat Behavior\n\n- 少说话，有价值时再回复。\n"
    (profile / "workspace/AGENTS.md").write_text(agents, encoding="utf-8")
    (profile / "config.yaml").write_text(
        "memory:\n  memory_char_limit: 20000\n  user_char_limit: 4000\n",
        encoding="utf-8",
    )
    (profile / "skills/legacy-memory-search/SKILL.md").write_text(
        "# Search\n", encoding="utf-8"
    )
    return profile


def test_curators_remove_legacy_automation_and_invalid_tool_names():
    memory = curate_memory(
        "# MEMORY.md\n\n- 稳定事实。\n\n# Deep Sleep\n\nConversation Summary: x\n"
    )
    soul = render_soul(
        "feishu-dm-test",
        "- **Name:** TODEClaw-测试\nUse sessions_spawn.\n",
    )
    agents = render_agents(
        "feishu-group-test",
        "Read workspace/memory. Use @openclaw/feishu and sessions_spawn.\n"
        "## 飞书表格渲染规则\n直接写 pipe table。\n",
    )

    assert "稳定事实" in memory
    assert "Deep Sleep" not in memory
    assert "Conversation Summary" not in memory
    assert "TODEClaw" not in soul
    assert "sessions_spawn" not in soul
    assert "workspace/memory" not in agents
    assert "@openclaw" not in agents
    assert "飞书表格" in agents


def test_memory_curator_drops_dated_openclaw_and_point_in_time_sections():
    memory = curate_memory(
        "# MEMORY.md - 长期记忆\n\n"
        "- 长期偏好：关注电力能源行业。\n\n"
        "### 2026-03-16\n\n"
        "- 访问控制规则：使用旧 OpenClaw Owner ID。\n\n"
        "### 2026-03-11\n\n"
        "- 股市关注：上证指数 +0.65%。\n\n"
        "## 工作区约定\n\n"
        "- 读取旧 memory/ 目录。\n"
    )

    assert "长期偏好" in memory
    assert "2026-03-16" not in memory
    assert "访问控制" not in memory
    assert "上证指数" not in memory
    assert "工作区约定" not in memory
    assert "重要事件与决策" not in curate_memory(
        "# MEMORY.md\n\n- 稳定事实。\n\n## 重要事件与决策\n"
    )


def test_apply_is_bounded_backed_up_and_restorable(tmp_path):
    root = tmp_path / "hermes"
    root.mkdir()
    (root / "config.yaml").write_text("memory: {}\n", encoding="utf-8")
    first = _profile(root, "feishu-dm-test")
    second = _profile(root, "feishu-group-test", group=True)
    (root / "shared-skills").mkdir()
    original_memory = (first / "memories/MEMORY.md").read_bytes()

    plan = build_plan(root)

    assert plan.report["summary"]["profile_count"] == 2
    assert plan.report["summary"]["memory_chars_after"] < plan.report["summary"]["memory_chars_before"]
    backup = root / "backups/context-test"
    receipt = apply_plan(plan, backup_dir=backup)

    assert receipt["backup"] == str(backup)
    for profile in (first, second):
        memory = (profile / "memories/MEMORY.md").read_text(encoding="utf-8")
        user = (profile / "memories/USER.md").read_text(encoding="utf-8")
        config = yaml.safe_load((profile / "config.yaml").read_text())
        assert len(memory) <= MEMORY_LIMIT
        assert len(user) <= USER_LIMIT
        assert "Deep Sleep" not in memory
        assert config["memory"]["memory_char_limit"] == MEMORY_LIMIT
        assert config["memory"]["user_char_limit"] == USER_LIMIT
        assert (profile / ".no-bundled-skills").is_file()
    assert (root / "shared-skills/legacy-memory-search/SKILL.md").is_file()
    assert json.loads((backup / "before.json").read_text())["schema_version"]

    shared = root / "shared-skills"
    for item in shared.rglob("*"):
        item.chmod(0o550 if item.is_dir() else 0o440)
    shared.chmod(0o550)

    result = restore_backup(root, backup)

    assert result["status"] == "restored"
    assert (first / "memories/MEMORY.md").read_bytes() == original_memory
    assert not (root / "shared-skills/legacy-memory-search").exists()
    assert shared.stat().st_mode & 0o777 == 0o550


def test_deepseek_drafts_override_fallback_and_are_hash_locked(tmp_path):
    root = tmp_path / "hermes"
    root.mkdir()
    (root / "config.yaml").write_text("memory: {}\n", encoding="utf-8")
    profile = _profile(root, "feishu-dm-test")
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    paths = {
        "memory": profile / "memories/MEMORY.md",
        "user": profile / "memories/USER.md",
        "soul": profile / "SOUL.md",
        "agents": profile / "workspace/AGENTS.md",
    }
    hashes = {name: __import__("hashlib").sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}
    (drafts / "feishu-dm-test.json").write_text(
        json.dumps(
            {
                "schema_version": "tode68-profile-context-draft/v1",
                "profile": "feishu-dm-test",
                "model": "deepseek-v4-flash",
                "input_hashes": hashes,
                "output": {
                    "memory": "# MEMORY.md\n- DeepSeek 保留的事实。\n",
                    "user": "# USER.md\n- DeepSeek 用户画像。\n",
                    "soul": "# SOUL.md\n- DeepSeek 身份。\n",
                    "agents": "# AGENTS.md\n- DeepSeek 规则。\n",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    plan = build_plan(root, drafts_dir=drafts)

    memory_write = next(action for action in plan.writes if action.path == paths["memory"])
    assert "DeepSeek 保留的事实" in memory_write.payload.decode()
    assert plan.report["rewrite_source"] == "deepseek-v4-flash"
