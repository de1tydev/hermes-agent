from __future__ import annotations

import json
from pathlib import Path

from scripts.generate_tode68_profile_context_drafts import (
    SCHEMA,
    generate_profile_draft,
    generate_profile_draft_split,
    parse_model_json,
    redact_for_model,
    validate_rewrite,
)


def _profile(root: Path) -> Path:
    profile = root / "profiles/feishu-dm-test"
    (profile / "memories").mkdir(parents=True)
    (profile / "workspace").mkdir()
    (profile / "memories/MEMORY.md").write_text("secret token=abc123456789\n", encoding="utf-8")
    (profile / "memories/USER.md").write_text("# USER.md\n", encoding="utf-8")
    (profile / "SOUL.md").write_text("# SOUL.md\n", encoding="utf-8")
    (profile / "workspace/AGENTS.md").write_text("# AGENTS.md\n", encoding="utf-8")
    return profile


def test_redaction_and_json_validation_are_secret_safe():
    assert "abc123456789" not in redact_for_model("token=abc123456789")
    sanitized = redact_for_model(
        "OpenClaw Owner ou_raw_identity job_id=abc memory/"
    )
    assert "OpenClaw" not in sanitized
    assert "ou_raw_identity" not in sanitized
    assert "job_id" not in sanitized
    assert "memory/" not in sanitized
    assert "<REDACTED_ID>" not in sanitized
    data = parse_model_json(
        "```json\n"
        + json.dumps(
            {
                "memory": "# MEMORY.md\n- 稳定事实。",
                "user": "# USER.md\n- 偏好简洁。",
                "soul": "# SOUL.md\n- Hermes Agent。",
                "agents": "# AGENTS.md\n- 使用 session_search。",
                "removed_summary": ["删除旧会话"],
            },
            ensure_ascii=False,
        )
        + "\n```"
    )
    output = validate_rewrite(data, agents_existed=True)
    assert output["memory"].endswith("\n")
    assert output["removed_summary"] == ["删除旧会话"]

    data["agents"] = "# AGENTS.md\n- 仅 Owner 可读取记忆。"
    try:
        validate_rewrite(data, agents_existed=True)
    except ValueError as exc:
        assert "retired runtime artifact" in str(exc)
    else:
        raise AssertionError("retired Owner policy should be rejected")

    data["agents"] = "# AGENTS.md\n- 维护笔记写入 memory/。"
    try:
        validate_rewrite(data, agents_existed=True)
    except ValueError as exc:
        assert "retired runtime artifact" in str(exc)
    else:
        raise AssertionError("retired workspace memory path should be rejected")

    data["agents"] = "# AGENTS.md\n- sender=ou_raw_identity，job_id=abc。"
    try:
        validate_rewrite(data, agents_existed=True)
    except ValueError as exc:
        assert "retired runtime artifact" in str(exc)
    else:
        raise AssertionError("raw identifiers should be rejected")


def test_generate_profile_draft_records_hashes_and_usage(tmp_path):
    profile = _profile(tmp_path)

    def fake_call(_profile, _system, user_prompt):
        assert "abc123456789" not in user_prompt
        return json.dumps(
            {
                "memory": "# MEMORY.md\n- 稳定事实。",
                "user": "# USER.md\n- 偏好简洁。",
                "soul": "# SOUL.md\n- Hermes Agent。",
                "agents": "# AGENTS.md\n- 使用 session_search。",
                "removed_summary": ["删除旧会话"],
            },
            ensure_ascii=False,
        ), {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168}

    draft = generate_profile_draft(tmp_path, profile, fake_call)

    assert draft["schema_version"] == SCHEMA
    assert draft["model"] == "deepseek-v4-flash"
    assert draft["usage"]["total_tokens"] == 168
    assert draft["input_hashes"]["memory"]


def test_split_generation_calls_each_existing_file_and_assembles_draft(tmp_path):
    profile = _profile(tmp_path)
    calls = []

    def fake_call(_profile, system_prompt, user_prompt):
        source = json.loads(user_prompt)
        calls.append(source["file_type"])
        return json.dumps(
            {
                "content": f"# {source['file_type'].upper()}\n- 语义改写。",
                "removed_summary": [f"清理 {source['file_type']}"],
            },
            ensure_ascii=False,
        ), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    draft = generate_profile_draft_split(profile, fake_call)

    assert calls == ["memory", "user", "soul", "agents"]
    assert draft["generation_mode"] == "split-files"
    assert draft["usage"]["total_tokens"] == 60
    assert "语义改写" in draft["output"]["agents"]
