from pathlib import Path

import yaml

from scripts.migrate_tode68_shared_skills import WORKSPACE_RULES, migrate


def _skill(root: Path, name: str, body: str = "same") -> None:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test\n---\n\n{body}\n",
        encoding="utf-8",
    )


def _legacy_skill(root: Path, name: str) -> None:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")


def test_migrate_shared_skills_preserves_overrides_and_disabled_subset(tmp_path):
    _skill(tmp_path / "shared-skills", "common")
    _skill(tmp_path / "shared-skills", "optional")
    _legacy_skill(tmp_path / "shared-skills", "legacy")
    first = tmp_path / "profiles/first"
    second = tmp_path / "profiles/second"
    for profile in (first, second):
        (profile / "workspace").mkdir(parents=True)
        (profile / "config.yaml").write_text("model:\n  default: test\n", encoding="utf-8")
    _skill(first / "skills", "common")
    _legacy_skill(first / "skills", "legacy")
    _skill(second / "skills", "common", body="local override")
    _legacy_skill(second / "skills", "legacy")
    (first / "workspace/AGENTS.md").write_text("# Existing\n", encoding="utf-8")

    dry_run = migrate(tmp_path, None, apply=False)
    assert dry_run["identical_copies"] == 3
    assert (first / "skills/common").is_dir()

    backup = tmp_path / "backups/migration"
    result = migrate(tmp_path, backup, apply=True)
    assert result["differing_copies"] == 1
    assert not (first / "skills/common").exists()
    assert (backup / "profiles/first/skills/common/SKILL.md").is_file()
    assert (second / "skills/common/SKILL.md").is_file()
    for profile in (first, second):
        config = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
        assert config["skills"]["external_dirs"] == [str(tmp_path / "shared-skills")]
        assert config["skills"]["disabled"] == ["optional"]
        assert WORKSPACE_RULES in (profile / "workspace/AGENTS.md").read_text(encoding="utf-8")

    rerun = migrate(tmp_path, None, apply=False)
    assert rerun["disabled_additions"] == 0
