import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def enforced_profiles(tmp_path, monkeypatch):
    root = tmp_path / "hermes"
    alice = root / "profiles" / "alice"
    bob = root / "profiles" / "bob"
    for home in (alice, bob):
        (home / "workspace").mkdir(parents=True)
        (home / "skills").mkdir()
    (alice / "workspace" / "own.txt").write_text("own", encoding="utf-8")
    (bob / "workspace" / "secret.txt").write_text("secret", encoding="utf-8")

    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("HERMES_PROFILE_ISOLATION_ENFORCED", "true")
    monkeypatch.setenv("HERMES_PROFILE_ADMIN_USER_IDS", "admin-user")
    monkeypatch.setenv("HERMES_SESSION_PROFILE", "alice")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "ordinary-user")
    return root, alice, bob


def test_native_file_tools_block_reads_and_cross_profile_flag(
    enforced_profiles, monkeypatch
):
    from tools.file_tools import read_file_tool, write_file_tool

    _, alice, bob = enforced_profiles
    own = json.loads(read_file_tool(str(alice / "workspace" / "own.txt")))
    assert not own.get("error")

    denied_read = json.loads(read_file_tool(str(bob / "workspace" / "secret.txt")))
    assert "cross-profile" in denied_read["error"].lower()

    denied_write = json.loads(
        write_file_tool(
            str(bob / "workspace" / "secret.txt"),
            "changed",
            cross_profile=True,
        )
    )
    assert "cross-profile" in denied_write["error"].lower()
    assert (bob / "workspace" / "secret.txt").read_text(encoding="utf-8") == "secret"

    monkeypatch.setenv("HERMES_SESSION_USER_ID", "admin-user")
    allowed = json.loads(
        write_file_tool(
            str(bob / "workspace" / "secret.txt"),
            "admin-change",
            cross_profile=True,
        )
    )
    assert not allowed.get("error"), allowed


def test_pre_tool_policy_runs_before_configurable_hooks(enforced_profiles, monkeypatch):
    from hermes_cli import plugins

    root, _, _ = enforced_profiles
    invoked = False

    def should_not_run(*args, **kwargs):
        nonlocal invoked
        invoked = True
        return []

    monkeypatch.setattr("hermes_cli.lifecycle.invoke_hook", should_not_run)
    directive = plugins._get_pre_tool_call_directive_details(
        "terminal", {"command": f"cat {root}/profiles/bob/workspace/secret.txt"}
    )
    assert directive.action == "block"
    assert not invoked


def test_session_search_cannot_scan_or_open_another_profile(
    enforced_profiles, monkeypatch
):
    from agent.profile_access import ProfileAccessDenied
    from hermes_cli import profiles as profiles_mod
    from tools.session_search_tool import _locate_session_db, _resolve_profile_db

    with pytest.raises(ProfileAccessDenied):
        _resolve_profile_db("bob")

    def forbidden_list():
        raise AssertionError("non-admin session search enumerated all profiles")

    monkeypatch.setattr(profiles_mod, "list_profiles", forbidden_list)
    assert _locate_session_db("missing-session") == (None, None)


def test_skill_lookup_does_not_disclose_other_profiles(enforced_profiles):
    from tools.skill_manager_tool import _find_skill_in_other_profiles

    _, _, bob = enforced_profiles
    skill = bob / "skills" / "private-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# private", encoding="utf-8")
    assert _find_skill_in_other_profiles("private-skill") == []


def test_process_tool_rejects_foreign_session(enforced_profiles, monkeypatch):
    from tools import process_registry as module

    foreign = SimpleNamespace(task_id="task-b", session_key="session-b")
    monkeypatch.setattr(module.process_registry, "get", lambda _sid: foreign)
    monkeypatch.setattr(
        "tools.approval.get_current_session_key", lambda default="": "session-a"
    )
    result = json.loads(
        module._handle_process(
            {"action": "poll", "session_id": "proc_foreign"}, task_id="task-a"
        )
    )
    assert "cross-profile" in result["error"].lower()


def test_local_environment_wraps_foreground_shell(enforced_profiles, monkeypatch):
    from tools.environments import local as module

    _, alice, _ = enforced_profiles
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(pid=424242)

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(module.os, "getpgid", lambda _pid: 424242)
    environment = object.__new__(module.LocalEnvironment)
    environment.cwd = str(alice / "workspace")
    environment.env = {}

    assert Path(environment.get_temp_dir()).is_relative_to(alice)
    environment._run_bash("pwd")
    argv = captured["args"]
    assert Path(argv[1]).name == "profile_sandbox.py"
    assert argv[-3:] == [module._find_bash(), "-c", "pwd"]


def test_admin_foreground_shell_is_not_sandbox_wrapped(
    enforced_profiles, monkeypatch
):
    from agent.profile_access import sandbox_argv

    monkeypatch.setenv("HERMES_SESSION_USER_ID", "admin-user")
    argv = ["/bin/bash", "-c", "pwd"]
    assert sandbox_argv(argv) == argv


def test_local_environment_runs_normal_commands_but_blocks_other_profile(
    enforced_profiles,
):
    from tools.environments.local import LocalEnvironment

    _, alice, bob = enforced_profiles
    environment = LocalEnvironment(cwd=str(alice / "workspace"), timeout=10)
    try:
        result = environment.execute("printf sandbox-ok")
        assert result["returncode"] == 0
        assert "sandbox-ok" in result["output"]
        denied = environment.execute(
            f"cat {bob / 'workspace' / 'secret.txt'}"
        )
        assert denied["returncode"] != 0
        assert "Permission denied" in denied["output"]
        assert denied["output"].strip() != "secret"
    finally:
        environment.cleanup()


def test_search_files_resolves_relative_traversal_before_access(
    enforced_profiles, monkeypatch
):
    from tools.file_tools import search_tool
    from tools.terminal_tool import register_task_env_overrides

    _, alice, _ = enforced_profiles
    task_id = "profile-search-test"
    register_task_env_overrides(task_id, {"cwd": str(alice / "workspace")})
    result = json.loads(
        search_tool(
            pattern="secret",
            path="../../bob",
            target="content",
            task_id=task_id,
        )
    )
    assert "cross-profile" in result["error"].lower()


def test_execute_code_child_cannot_read_other_profile(
    enforced_profiles, monkeypatch
):
    from tools import code_execution_tool as module
    from tools.terminal_tool import register_task_env_overrides

    _, alice, bob = enforced_profiles
    task_id = "profile-code-test"
    register_task_env_overrides(task_id, {"cwd": str(alice / "workspace")})
    monkeypatch.setattr(module, "_resolve_child_python", lambda _mode: "/usr/bin/python3")
    monkeypatch.setattr(
        "tools.approval.check_execute_code_guard",
        lambda *args, **kwargs: {"approved": True},
    )

    result = json.loads(
        module.execute_code(
            f"from pathlib import Path\nprint(Path({str(bob / 'workspace' / 'secret.txt')!r}).read_text())",
            task_id=task_id,
            enabled_tools=[],
        )
    )
    combined = f"{result.get('output', '')}\n{result.get('error', '')}"
    assert "secret" not in result.get("output", "").split("--- stderr ---", 1)[0]
    assert "PermissionError" in combined


def test_non_admin_browser_cannot_use_file_scheme_or_javascript(
    enforced_profiles,
):
    from agent.profile_access import get_pre_tool_call_block_message

    assert get_pre_tool_call_block_message(
        "browser_navigate", {"url": "file:///opt/data/profiles/bob/state.db"}
    )
    assert get_pre_tool_call_block_message(
        "browser_console", {"expression": "window.location.href"}
    )
