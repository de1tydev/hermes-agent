import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gateway.profile_routing import ProfileRoute
from gateway.run import GatewayRunner
from gateway.session import Platform, SessionSource, build_session_key


def _runner(tmp_path: Path, *, authorized: bool = True) -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(
        multiplex_profiles=True,
        multiplex_profile_allowlist=None,
        profile_routes=[],
        platforms={
            Platform.FEISHU: SimpleNamespace(
                extra={"profile_auto_provision": True},
            )
        },
    )
    runner._multiplex_primary_home = tmp_path
    runner._is_user_authorized_for_source = lambda _source: authorized
    return runner


def _dm(user_id: str = "ou_new_user") -> SessionSource:
    return SessionSource(
        platform=Platform.FEISHU,
        chat_id="oc_dm",
        chat_type="dm",
        user_id=user_id,
    )


def _group(chat_id: str = "oc_new_group") -> SessionSource:
    return SessionSource(
        platform=Platform.FEISHU,
        chat_id=chat_id,
        chat_type="group",
        user_id="ou_group_member",
    )


@pytest.mark.asyncio
async def test_first_contact_uses_one_profile_namespace_end_to_end(tmp_path):
    runner = _runner(tmp_path)
    first = _dm()
    second = _dm()

    with patch(
        "hermes_cli.profiles._get_default_hermes_home",
        return_value=tmp_path,
    ):
        prepared_first, prepared_second = await asyncio.gather(
            runner.prepare_inbound_source(first),
            runner.prepare_inbound_source(second),
        )

    assert prepared_first is first
    assert prepared_second is second
    assert first.profile == second.profile
    assert first.profile.startswith("feishu-dm-")
    assert build_session_key(first, profile=first.profile) == build_session_key(
        second, profile=second.profile
    )
    assert (tmp_path / "profiles" / first.profile).is_dir()

    registry_path = tmp_path / "state" / "profile-identity-registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry["schema_version"] == "hermes-profile-identity-registry/v1"
    assert len(registry["bindings"]) == 1
    assert next(iter(registry["bindings"].values()))["profile"] == first.profile
    assert registry_path.stat().st_mode & 0o777 == 0o600
    marker = tmp_path / "profiles" / first.profile / ".hermes-auto-profile.json"
    assert marker.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_authorized_unknown_group_uses_chat_identity(tmp_path):
    runner = _runner(tmp_path)
    first = _group()
    another_member = _group()
    another_member.user_id = "ou_other_member"

    with patch(
        "hermes_cli.profiles._get_default_hermes_home",
        return_value=tmp_path,
    ):
        assert await runner.prepare_inbound_source(first) is first
        assert await runner.prepare_inbound_source(another_member) is another_member

    assert first.profile == another_member.profile
    assert first.profile.startswith("feishu-group-")


@pytest.mark.asyncio
async def test_unauthorized_unknown_identity_has_zero_profile_side_effects(tmp_path):
    runner = _runner(tmp_path, authorized=False)
    source = _dm("ou_unauthorized")

    assert await runner.prepare_inbound_source(source) is None
    assert source.profile is None
    assert not (tmp_path / "profiles").exists()
    assert not (tmp_path / "state").exists()
    assert source.profile_prepare_rejected == "unauthorized"


@pytest.mark.asyncio
async def test_registry_write_failure_fails_closed_without_default_fallback(tmp_path):
    runner = _runner(tmp_path)
    source = _dm("ou_registry_failure")

    with patch(
        "hermes_cli.profiles._get_default_hermes_home",
        return_value=tmp_path,
    ), patch(
        "gateway.profile_provisioning.ProfileIdentityRegistry._write_registry",
        side_effect=OSError("disk full"),
    ):
        assert await runner.prepare_inbound_source(source) is None

    assert source.profile is None
    assert source.profile_prepare_rejected == "profile_provision_failed"


@pytest.mark.asyncio
async def test_static_dynamic_conflict_is_rejected(tmp_path):
    runner = _runner(tmp_path)
    source = _dm("ou_conflict")
    runner.config.profile_routes = [
        ProfileRoute(
            name="static",
            platform="feishu",
            profile="static-profile",
            user_id="ou_conflict",
        )
    ]
    source.profile = "static-profile"

    from gateway.profile_provisioning import ProfileIdentityRegistry

    registry = ProfileIdentityRegistry(tmp_path)
    registry_path = tmp_path / "state" / "profile-identity-registry.json"
    registry_path.parent.mkdir(parents=True)
    key, digest, kind = registry.identity_for_source(source)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": registry.SCHEMA_VERSION,
                "bindings": {
                    key: {
                        "platform": "feishu",
                        "kind": kind,
                        "identity_digest": digest,
                        "profile": "dynamic-profile",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert await runner.prepare_inbound_source(source) is None
    assert source.profile == "static-profile"
    assert source.profile_prepare_rejected == "profile_route_conflict"


@pytest.mark.asyncio
async def test_multiplex_off_preserves_legacy_source_without_auth_or_writes(tmp_path):
    runner = _runner(tmp_path)
    runner.config.multiplex_profiles = False
    runner._is_user_authorized_for_source = lambda _source: (_ for _ in ()).throw(
        AssertionError("legacy path must not pre-authorize")
    )
    source = _dm("ou_legacy")

    assert await runner.prepare_inbound_source(source) is source
    assert source.profile is None
    assert not (tmp_path / "profiles").exists()
    assert not (tmp_path / "state").exists()


@pytest.mark.asyncio
async def test_platform_without_auto_provision_preserves_legacy_ingress(tmp_path):
    runner = _runner(tmp_path)
    runner.config.platforms[Platform.FEISHU].extra["profile_auto_provision"] = False
    runner._is_user_authorized_for_source = lambda _source: (_ for _ in ()).throw(
        AssertionError("disabled auto-provision must not pre-authorize")
    )
    source = _dm("ou_disabled")

    assert await runner.prepare_inbound_source(source) is source
    assert source.profile is None
    assert not (tmp_path / "profiles").exists()
    assert not (tmp_path / "state").exists()


@pytest.mark.asyncio
async def test_generated_profile_outside_explicit_allowlist_is_rejected(tmp_path):
    runner = _runner(tmp_path)
    runner.config.multiplex_profile_allowlist = []
    source = _dm("ou_not_served")

    assert await runner.prepare_inbound_source(source) is None
    assert source.profile is None
    assert source.profile_prepare_rejected == "profile_not_served"
    assert not (tmp_path / "profiles").exists()
