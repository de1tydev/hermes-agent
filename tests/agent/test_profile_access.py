from pathlib import Path

import pytest


@pytest.fixture
def isolated_profiles(tmp_path, monkeypatch):
    root = tmp_path / "hermes"
    alice = root / "profiles" / "alice"
    bob = root / "profiles" / "bob"
    shared = root / "shared-skills"
    for path in (alice, bob, shared):
        path.mkdir(parents=True)

    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("HERMES_PROFILE_ISOLATION_ENFORCED", "true")
    monkeypatch.setenv("HERMES_PROFILE_ADMIN_USER_IDS", "admin-user")
    monkeypatch.setenv("HERMES_SESSION_PROFILE", "alice")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "ordinary-user")
    return root, alice, bob, shared


def test_non_admin_can_access_only_own_profile_and_shared_reads(isolated_profiles):
    from agent.profile_access import ProfileAccessDenied, authorize_path

    _, alice, bob, shared = isolated_profiles
    assert authorize_path(alice / "memories" / "MEMORY.md", operation="read")
    assert authorize_path(shared / "tool" / "SKILL.md", operation="read")

    with pytest.raises(ProfileAccessDenied):
        authorize_path(bob / "memories" / "MEMORY.md", operation="read")
    with pytest.raises(ProfileAccessDenied):
        authorize_path(shared / "tool" / "SKILL.md", operation="write")


def test_non_admin_cannot_enumerate_profiles_root(isolated_profiles):
    from agent.profile_access import ProfileAccessDenied, authorize_path

    root, _, _, _ = isolated_profiles
    with pytest.raises(ProfileAccessDenied):
        authorize_path(root / "profiles", operation="read")
    with pytest.raises(ProfileAccessDenied):
        authorize_path(root, operation="read")


def test_admin_is_bound_to_stable_user_id_not_profile_name(
    isolated_profiles, monkeypatch
):
    from agent.profile_access import ProfileAccessDenied, authorize_path

    _, _, bob, _ = isolated_profiles

    # Merely claiming the administrator's DM profile is not authorization.
    monkeypatch.setenv("HERMES_SESSION_PROFILE", "feishu-dm-admin")
    with pytest.raises(ProfileAccessDenied):
        authorize_path(bob / "state.db", operation="read")

    # The stable Feishu user id authorizes the administrator from any chat,
    # including a group profile.
    monkeypatch.setenv("HERMES_SESSION_PROFILE", "feishu-group-team")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "admin-user")
    assert authorize_path(bob / "state.db", operation="read")


def test_explicit_profile_arguments_are_blocked_for_non_admin(isolated_profiles):
    from agent.profile_access import get_pre_tool_call_block_message

    assert get_pre_tool_call_block_message(
        "session_search", {"profile": "bob", "query": "secret"}
    )
    assert get_pre_tool_call_block_message(
        "read_file", {"path": "/does/not/matter", "profile_name": "bob"}
    )


def test_explicit_other_profile_path_in_terminal_command_is_blocked(
    isolated_profiles,
):
    from agent.profile_access import get_pre_tool_call_block_message

    root, _, _, _ = isolated_profiles
    message = get_pre_tool_call_block_message(
        "terminal",
        {"command": f"find {root}/profiles/bob -type f -maxdepth 2"},
    )
    assert message
    assert "cross-profile" in message.lower()
