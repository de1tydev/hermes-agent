"""Regression coverage for multiplex cron delivery isolation.

Profile-local Home Channels are canonical in multiplex mode.  A value left in
the process environment by another profile must never redirect a cron result,
and a live root Gateway adapter must satisfy delivery preflight even though the
owning profile intentionally carries no transport credentials.
"""

from pathlib import Path
from concurrent.futures import Future
from unittest.mock import AsyncMock, MagicMock, patch

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


def test_live_gateway_adapter_delivers_when_profile_transport_is_disabled():
    """The root live adapter, not Profile credentials, owns cron delivery."""
    from gateway.config import GatewayConfig, PlatformConfig

    adapter = AsyncMock()
    adapter.send.return_value = MagicMock(success=True, message_id="om_test")
    config = GatewayConfig(
        platforms={Platform.FEISHU: PlatformConfig(enabled=False)}
    )
    loop = MagicMock()
    loop.is_running.return_value = True

    def run_coro(coro, _loop):
        import asyncio

        future = Future()
        try:
            future.set_result(asyncio.run(coro))
        except BaseException as exc:  # noqa: BLE001
            future.set_exception(exc)
        return future

    job = {
        "id": "multiplex-live-delivery",
        "deliver": "origin",
        "origin": {"platform": "feishu", "chat_id": "oc_owner_chat"},
    }

    set_multiplex_active(True)
    try:
        with (
            patch("gateway.config.load_gateway_config", return_value=config),
            patch("cron.scheduler.load_config", return_value={"cron": {"wrap_response": False}}),
            patch("asyncio.run_coroutine_threadsafe", side_effect=run_coro),
            patch("tools.send_message_tool._send_to_platform", new=AsyncMock()) as standalone,
        ):
            result = sched._deliver_result(
                job,
                "scheduled result",
                adapters={Platform.FEISHU: adapter},
                loop=loop,
            )
    finally:
        set_multiplex_active(False)

    assert result is None
    adapter.send.assert_awaited_once()
    standalone.assert_not_awaited()
