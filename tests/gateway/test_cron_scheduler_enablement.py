"""Gateway-local cron scheduler ownership controls."""

import gateway.run as gateway_run


def test_gateway_cron_scheduler_enabled_by_default(monkeypatch):
    monkeypatch.delenv("HERMES_GATEWAY_CRON_ENABLED", raising=False)
    assert gateway_run._gateway_cron_scheduler_enabled() is True


def test_gateway_cron_scheduler_can_be_disabled_for_secondary(monkeypatch):
    for value in ("false", "0", "no", "off"):
        monkeypatch.setenv("HERMES_GATEWAY_CRON_ENABLED", value)
        assert gateway_run._gateway_cron_scheduler_enabled() is False
