from __future__ import annotations

import os
import re
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

from scripts.repair_tode68_personal_skills import PERSONAL_SKILLS, run
from tools.skill_linter import has_errors, lint_skill


ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "deploy" / "tode68" / "personal-skills"
OFFICECLI_SKILL = ROOT / "deploy" / "tode68" / "runtime-skills" / "officecli"
SECRET_LITERAL = re.compile(
    rb"\b(?:sk|xox[baprs]|gh[pousr])-[A-Za-z0-9_-]{8,}"
    rb"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)


def _seed_skills(root: Path) -> None:
    for name in PERSONAL_SKILLS:
        skill = root / name
        skill.mkdir(parents=True)
        frontmatter = (
            f"---\nname: {name}\ndescription: 测试 {name}。\n---\n"
        )
        if name == "ask-code":
            frontmatter = (
                "---\nname: ask-code\ndescription: 查询受控私有代码库。\n"
                "required_environment_variables:\n"
                "  - name: ASK_CODE_URL\n    prompt: Ask Code URL\n"
                "  - name: ASK_CODE_API_KEY\n    prompt: Ask Code API key\n"
                "---\n"
            )
            (skill / "ask_code.py").write_text("print('ok')\n", encoding="utf-8")
        (skill / "SKILL.md").write_text(frontmatter, encoding="utf-8")


def test_personal_skill_assets_are_complete_loadable_and_secret_free():
    actual = {path.name for path in SKILLS.iterdir() if path.is_dir()}
    assert actual == PERSONAL_SKILLS
    for name in sorted(PERSONAL_SKILLS):
        skill = SKILLS / name
        assert not has_errors(lint_skill(skill / "SKILL.md"))
        assert not any(path.is_symlink() for path in skill.rglob("*"))
        for path in skill.rglob("*"):
            assert path.name not in {".env", "skill.env", ".ask_code_state.json"}
            if path.is_file() and path.stat().st_size <= 2_000_000:
                assert not SECRET_LITERAL.search(path.read_bytes())


def test_ask_code_asset_uses_hermes_contract_and_dry_run():
    skill = SKILLS / "ask-code"
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    assert "required_environment_variables:" in text
    assert "ASK_CODE_URL" in text
    assert "ASK_CODE_API_KEY" in text
    combined = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in skill.rglob("*")
        if path.is_file()
    )
    assert "OpenClaw" not in combined
    assert ".agents/skills" not in combined
    result = subprocess.run(
        [
            sys.executable,
            str(skill / "ask_code.py"),
            "--project",
            "teap",
            "--caller",
            "test",
            "--dry-run",
            "How is authentication wired?",
        ],
        env={
            "ASK_CODE_URL": "http://127.0.0.1:8891",
            "ASK_CODE_API_KEY": "synthetic",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert '"project": "teap"' in result.stdout


def test_officecli_runtime_skill_asset_is_loadable_and_secret_free():
    assert not has_errors(lint_skill(OFFICECLI_SKILL / "SKILL.md"))
    text = (OFFICECLI_SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "exec /opt/data/bin/officecli" in text
    assert "不要直接运行裸 `officecli`" in text
    assert "不要自行安装 ICU 或放宽 `/proc`" in text
    assert not any(path.is_symlink() for path in OFFICECLI_SKILL.rglob("*"))
    for path in OFFICECLI_SKILL.rglob("*"):
        assert path.name not in {".env", "skill.env", ".ask_code_state.json"}
        if path.is_file() and path.stat().st_size <= 2_000_000:
            assert not SECRET_LITERAL.search(path.read_bytes())


def test_lark_shared_keeps_workspace_scoped_auth_config():
    text = (SKILLS / "lark-shared/SKILL.md").read_text(encoding="utf-8")
    assert "LARKSUITE_CLI_CONFIG_DIR" in text
    assert "agent-data/lark-cli" in text
    assert "禁止使用全局" in text
    assert "不得借用 Gateway transport 应用" in text
    assert "普通 Profile 不运行 `lark-cli config bind --source hermes`" in text


def test_repair_installs_personal_skills_and_scoped_ask_code_env(tmp_path):
    target = tmp_path / "target"
    (target / "shared-skills").mkdir(parents=True)
    (target / "shared-skills/existing").mkdir()
    (target / "shared-skills/existing/SKILL.md").write_text(
        "# existing\n", encoding="utf-8"
    )
    for name in ("alpha", "beta"):
        profile = target / "profiles" / name
        (profile / "skills").mkdir(parents=True)
        (profile / ".env").write_text("OPENAI_API_KEY=provider\n", encoding="utf-8")

    skills = tmp_path / "skills"
    _seed_skills(skills)
    personal = tmp_path / "personal"
    (personal / "ask-code").mkdir(parents=True)
    (personal / "ask-code/.env").write_text(
        "ASK_CODE_URL=http://192.168.50.55:8891\n"
        "ASK_CODE_API_KEY=synthetic-ask-code-key\n",
        encoding="utf-8",
    )
    officecli = tmp_path / "officecli"
    officecli.write_bytes(b"#!/bin/sh\necho 1.0.0\n")
    officecli.chmod(0o500)
    officecli_skill = tmp_path / "officecli-skill" / "officecli"
    officecli_skill.mkdir(parents=True)
    (officecli_skill / "SKILL.md").write_text(
        "---\nname: officecli\ndescription: 测试 Office CLI。\n---\n",
        encoding="utf-8",
    )

    receipt = run(
        Namespace(
            target=target,
            skills_source=skills,
            personal_skills_source=personal,
            officecli_binary=officecli,
            officecli_skill_source=officecli_skill,
            uid=os.getuid(),
            gid=os.getgid(),
        )
    )

    assert receipt["skills_activated"] == len(PERSONAL_SKILLS) == 24
    assert receipt["profiles_updated"] == 2
    assert (target / "shared-skills/existing/SKILL.md").is_file()
    for name in ("alpha", "beta"):
        profile = target / "profiles" / name
        assert {path.name for path in (profile / "skills").iterdir()} == (
            PERSONAL_SKILLS | {"officecli"}
        )
        env = (profile / ".env").read_text(encoding="utf-8")
        assert "OPENAI_API_KEY=provider" in env
        assert "ASK_CODE_URL=http://192.168.50.55:8891" in env
        assert "ASK_CODE_API_KEY=synthetic-ask-code-key" in env
        assert "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1" in env
    assert Path(receipt["backup"]).is_dir()
    assert receipt["officecli"]["binary_changed"] is True
    assert receipt["officecli"]["skill_destinations_changed"] == 3
    assert (target / "bin/officecli").is_file()
    assert (target / "bin/officecli").stat().st_mode & 0o777 == 0o550
    assert (target / "migration/personal-skill-repair-receipt.json").is_file()
