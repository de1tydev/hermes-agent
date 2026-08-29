"""Regression coverage for multiplex cron delivery isolation.

Profile-local Home Channels are canonical in multiplex mode.  A value left in
the process environment by another profile must never redirect a cron result,
and a live root Gateway adapter must satisfy delivery preflight even though the
owning profile intentionally carries no transport credentials.
"""

from pathlib import Path

from agent.secret_scope import set_multiplex_active
from cron import scheduler as sched
from gateway.config import Platform
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


def _write_profile_home(home: Path, chat_id: str) -> None:
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        "platforms:\n"
        "  feishu:\n"
        "    home_channel:\n"
        "      platform: feishu\n"
        f"      chat_id: {chat_id}\n"
        "      name: owning-profile\n",
        encoding="utf-8",
    )


def test_multiplex_delivery_uses_profile_home_not_stale_process_env(
    tmp_path, monkeypatch
):
    """A prior profile's env mirror cannot redirect another profile's cron."""
    owner_home = tmp_path / "owner"
    _write_profile_home(owner_home, "oc_owner_chat")
    monkeypatch.setenv("FEISHU_HOME_CHANNEL", "oc_wrong_prior_profile")

    set_multiplex_active(True)
    token = set_hermes_home_override(str(owner_home))
    try:
        assert sched._resolve_delivery_targets({"deliver": "feishu"}) == [
            {
                "platform": "feishu",
                "chat_id": "oc_owner_chat",
                "thread_id": None,
            }
        ]
    finally:
        reset_hermes_home_override(token)
        set_multiplex_active(False)


def test_live_gateway_adapter_satisfies_profile_delivery_preflight(monkeypatch):
    """Profiles need no duplicate Feishu credentials when Gateway is live."""
    disconnected_profile_config = type(
        "DisconnectedProfileConfig",
        (),
        {"get_connected_platforms": lambda self: []},
    )()
    monkeypatch.setattr(
        "gateway.config.load_gateway_config",
        lambda: disconnected_profile_config,
    )

    assert sched._preflight_check_delivery(
        {"deliver": "feishu"},
        adapters={Platform.FEISHU: object()},
    ) is None
