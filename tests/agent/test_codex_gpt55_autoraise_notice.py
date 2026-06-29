"""Tests for the Codex gpt-5.5 compaction autoraise notice."""

from __future__ import annotations

from agent import agent_init


def test_codex_gpt55_autoraise_notice_claims_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(agent_init, "get_hermes_home", lambda: tmp_path)

    autoraise = {"from": 0.80, "to": 0.85}

    assert agent_init._claim_codex_gpt55_autoraise_notice(autoraise) is True
    assert agent_init._claim_codex_gpt55_autoraise_notice(autoraise) is False


def test_codex_gpt55_autoraise_notice_claim_is_threshold_specific(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(agent_init, "get_hermes_home", lambda: tmp_path)

    assert (
        agent_init._claim_codex_gpt55_autoraise_notice({"from": 0.80, "to": 0.85})
        is True
    )
    assert (
        agent_init._claim_codex_gpt55_autoraise_notice({"from": 0.75, "to": 0.85})
        is True
    )
