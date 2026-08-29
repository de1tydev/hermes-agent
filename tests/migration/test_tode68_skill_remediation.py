from __future__ import annotations

import re
import os
import stat
from argparse import Namespace
from pathlib import Path

import yaml

from scripts.remediate_tode68_skills import run
from tools.skill_linter import has_errors, lint_skill


ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "deploy" / "tode68" / "shared-skills"
EXPECTED = {
    "ai-usage-report",
    "baidu-baike-data",
    "baidu-scholar-search-skill",
    "baidu-search",
    "best-image-generation",
    "gemini-image-simple",
    "lark-bot-ops",
    "nano-banana-openrouter",
    "qwen-image",
    "ragflow-skill",
    "self-improving",
    "service-status-timeline",
    "skill-vetter",
    "teap-cli-ops",
    "teap-test",
    "tode-jira",
    "zhipu-search",
}
SECRET_LITERAL = re.compile(
    rb"\b(?:sk|xox[baprs]|gh[pousr])-[A-Za-z0-9_-]{8,}"
    rb"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)


def test_remediated_skill_set_is_complete_and_loadable():
    actual = {path.name for path in SKILLS.iterdir() if path.is_dir()}
    assert actual == EXPECTED
    for name in sorted(EXPECTED):
        skill = SKILLS / name
        skill_md = skill / "SKILL.md"
        assert skill_md.is_file()
        assert not has_errors(lint_skill(skill_md))
        assert "openclaw" not in skill_md.read_text(encoding="utf-8").lower()
        assert not any(path.is_symlink() for path in skill.rglob("*"))


def test_remediated_skills_do_not_ship_runtime_or_secret_material():
    forbidden_parts = {".venv", "node_modules", "__pycache__", ".clawhub"}
    forbidden_names = {".env", "skill.env", "output.json"}
    for path in SKILLS.rglob("*"):
        assert forbidden_parts.isdisjoint(path.parts)
        assert path.name not in forbidden_names
        if path.is_file() and path.stat().st_size <= 2_000_000:
            assert not SECRET_LITERAL.search(path.read_bytes())


def test_api_skills_declare_profile_scoped_environment_variables():
    expected_vars = {
        "baidu-baike-data": "BAIDU_API_KEY",
        "baidu-scholar-search-skill": "BAIDU_API_KEY",
        "baidu-search": "BAIDU_API_KEY",
        "best-image-generation": "EVOLINK_API_KEY",
        "gemini-image-simple": "GEMINI_API_KEY",
        "nano-banana-openrouter": "OPENROUTER_API_KEY",
        "qwen-image": "DASHSCOPE_API_KEY",
        "ragflow-skill": "RAGFLOW_API_KEY",
        "teap-test": "GITEA_TOKEN",
        "tode-jira": "JIRA_API_TOKEN",
        "zhipu-search": "ZHIPU_API_KEY",
    }
    for skill, variable in expected_vars.items():
        text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "required_environment_variables:" in text
        assert f"name: {variable}" in text


def test_remediation_materializes_skills_credentials_tools_and_approval_policy(tmp_path):
    target = tmp_path / "target"
    profile = target / "profiles/example"
    profile.mkdir(parents=True)
    (target / "config.yaml").write_text("memory: {}\n", encoding="utf-8")
    (profile / "config.yaml").write_text(
        "memory: {}\nplatforms:\n  feishu:\n    enabled: true\n"
        "    home_channel:\n      platform: feishu\n      chat_id: oc_test\n",
        encoding="utf-8",
    )
    (profile / ".env").write_text("OPENAI_API_KEY=provider\n", encoding="utf-8")

    legacy = tmp_path / "legacy"
    (legacy / "workspace").mkdir(parents=True)
    (legacy / "skills/zhipu-search").mkdir(parents=True)
    (legacy / "openclaw.json").write_text(
        '{"skills":{"entries":{"baidu-search":{"env":{"BAIDU_API_KEY":"baidu"}}}}}',
        encoding="utf-8",
    )
    (legacy / "workspace/.env").write_text(
        "JIRA_API_TOKEN=jira-token\nJIRA_AUTH_TYPE=bearer\n", encoding="utf-8"
    )
    (legacy / "skills/zhipu-search/skill.env").write_text(
        "ZHIPU_API_KEY=zhipu\n", encoding="utf-8"
    )

    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("jira", "lark-cli", "teap"):
        tool = tools / name
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o500)
    tea = tmp_path / "tea.yml"
    tea.write_text("logins:\n  - token: gitea-token\n", encoding="utf-8")
    jira = tmp_path / "jira.yml"
    jira.write_text("server: https://jira.tode.ltd\n", encoding="utf-8")
    teap = tmp_path / "teap.json"
    teap.write_text('{"base_url":"https://develop.teap.tode.ltd"}\n', encoding="utf-8")
    lark_config = tmp_path / "lark-config"
    lark_share = tmp_path / "lark-share"
    lark_config.mkdir()
    lark_share.mkdir()
    (lark_config / "config.json").write_text("{}\n", encoding="utf-8")
    (lark_share / "state.json").write_text("{}\n", encoding="utf-8")

    receipt = run(
        Namespace(
            target=target,
            skills_source=SKILLS,
            legacy_root=legacy,
            tool_source=tools,
            tea_config=tea,
            jira_config=jira,
            teap_config=teap,
            lark_config=lark_config,
            lark_share=lark_share,
            uid=os.getuid(),
            gid=os.getgid(),
        )
    )

    assert receipt["skills_activated"] == 17
    assert receipt["profiles_updated"] == 1
    assert {p.name for p in (profile / "skills").iterdir()} == EXPECTED
    config = yaml.safe_load((profile / "config.yaml").read_text())
    assert config["approvals"]["destructive_slash_confirm"] is False
    assert "enabled" not in config["platforms"]["feishu"]
    env = (profile / ".env").read_text()
    assert "BAIDU_API_KEY=baidu" in env
    assert "GITEA_TOKEN=gitea-token" in env
    assert "JIRA_API_TOKEN=jira-token" in env
    assert "ZHIPU_API_KEY=zhipu" in env
    assert (target / "migration/skill-remediation-receipt.json").is_file()
    assert stat.S_IMODE((target / ".lark-cli/config.json").stat().st_mode) == 0o600
