from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.reconcile_tode68_dm_profiles import merge_profile


def _state_db(path: Path) -> bytes:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE sentinel(value TEXT NOT NULL)")
    connection.execute("INSERT INTO sentinel VALUES ('preserve-me')")
    connection.commit()
    connection.close()
    return path.read_bytes()


def test_merge_profile_restores_legacy_memory_search_without_touching_state(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    for name in ("memories", "legacy-memory", "workspace"):
        (source / name).mkdir()
    skill = source / "skills/legacy-memory-search"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Legacy memory search\n", encoding="utf-8")
    (source / ".env").write_text("OPENAI_API_KEY=test\n", encoding="utf-8")
    (target / ".env").write_text("OPENAI_API_KEY=test\n", encoding="utf-8")
    (source / "config.yaml").write_text("terminal: {}\n", encoding="utf-8")
    (target / "config.yaml").write_text("platforms: {}\n", encoding="utf-8")
    state_path = target / "state.db"
    state_before = _state_db(state_path)

    result = merge_profile(source, target)

    assert result["legacy_memory_search_restored"] is True
    assert (target / "skills/legacy-memory-search/SKILL.md").read_text() == (
        "# Legacy memory search\n"
    )
    assert state_path.read_bytes() == state_before
