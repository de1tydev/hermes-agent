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


def _assert_valid_published_profile(tmp_path: Path, profile: str) -> None:
    profile_dir = tmp_path / "profiles" / profile
    for dirname in ("workspace", "sessions", "skills", "memories"):
        assert (profile_dir / dirname).is_dir()
    assert (profile_dir / ".env").is_file()
    assert (profile_dir / "SOUL.md").is_file()
    assert (profile_dir / ".hermes-auto-profile.json").is_file()
    registry = json.loads(
        (tmp_path / "state" / "profile-identity-registry.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(registry["bindings"]) == 1
    assert next(iter(registry["bindings"].values()))["profile"] == profile

    from hermes_cli.profiles import profiles_to_serve

    with patch(
        "hermes_cli.profiles._get_default_hermes_home",
        return_value=tmp_path,
    ):
        served_names = [name for name, _path in profiles_to_serve(multiplex=True)]
    assert served_names.count(profile) == 1
    assert not any(".partial-" in name for name in served_names)


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
async def test_auto_profile_inherits_provider_but_not_transport_secret(tmp_path):
    import yaml

    runner = _runner(tmp_path)
    source = _dm("ou_safe_capabilities")
    (tmp_path / "config.yaml").write_text(
        "model:\n  default: model-a\n  provider: tode\n"
        "providers:\n  tode:\n    key_env: OPENAI_API_KEY\n"
        "terminal:\n  cwd: /opt/data/workspace\n"
        "gateway:\n  multiplex_profiles: true\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=provider-secret\n"
        "FEISHU_APP_ID=transport-id\n"
        "FEISHU_APP_SECRET=transport-secret\n",
        encoding="utf-8",
    )
    (tmp_path / "shared-skills/example").mkdir(parents=True)
    (tmp_path / "shared-skills/example/SKILL.md").write_text(
        "# Example\n", encoding="utf-8"
    )

    with patch(
        "hermes_cli.profiles._get_default_hermes_home",
        return_value=tmp_path,
    ):
        assert await runner.prepare_inbound_source(source) is source

    profile = tmp_path / "profiles" / source.profile
    env_text = (profile / ".env").read_text(encoding="utf-8")
    assert env_text == "OPENAI_API_KEY=provider-secret\n"
    assert "FEISHU" not in env_text
    config = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert "gateway" not in config
    assert config["model"] == {"default": "model-a", "provider": "tode"}
    assert config["terminal"]["cwd"] == str(profile / "workspace")
    assert config["platforms"]["feishu"]["home_channel"] == {
        "platform": "feishu",
        "chat_id": "oc_dm",
        "name": profile.name,
        "user_id": "ou_safe_capabilities",
    }
    assert "enabled" not in config["platforms"]["feishu"]
    assert (profile / "skills/example/SKILL.md").is_file()


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
    import yaml

    profile = tmp_path / "profiles" / first.profile
    config = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert config["platforms"]["feishu"]["home_channel"] == {
        "platform": "feishu",
        "chat_id": "oc_new_group",
        "name": first.profile,
        "user_id": "ou_group_member",
    }
    assert "enabled" not in config["platforms"]["feishu"]


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
async def test_marker_write_failure_recovers_on_next_authorized_retry(tmp_path):
    runner = _runner(tmp_path)
    first = _dm("ou_marker_failure")

    from gateway.profile_provisioning import ProfileIdentityRegistry

    original_write = ProfileIdentityRegistry._atomic_write_json
    failed = False

    def fail_first_marker(path, data):
        nonlocal failed
        if path.name == ProfileIdentityRegistry.MARKER_FILENAME and not failed:
            failed = True
            raise OSError("marker write failed")
        return original_write(path, data)

    with patch(
        "hermes_cli.profiles._get_default_hermes_home",
        return_value=tmp_path,
    ), patch.object(
        ProfileIdentityRegistry,
        "_atomic_write_json",
        side_effect=fail_first_marker,
    ):
        assert await runner.prepare_inbound_source(first) is None

    profile = ProfileIdentityRegistry.deterministic_profile_name(first)
    profile_dir = tmp_path / "profiles" / profile
    assert profile_dir.is_dir()
    assert not (profile_dir / ProfileIdentityRegistry.MARKER_FILENAME).exists()
    _key, identity_digest, _kind = ProfileIdentityRegistry.identity_for_source(first)
    claim_path = (
        tmp_path
        / ProfileIdentityRegistry.CLAIMS_RELATIVE_PATH
        / f"{identity_digest.removeprefix('sha256:')}.json"
    )
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    assert claim["status"] == "materialized"

    retry = _dm("ou_marker_failure")
    with patch(
        "hermes_cli.profiles._get_default_hermes_home",
        return_value=tmp_path,
    ):
        assert await runner.prepare_inbound_source(retry) is retry

    assert retry.profile == profile
    assert (profile_dir / ProfileIdentityRegistry.MARKER_FILENAME).is_file()
    registry = json.loads(
        (tmp_path / "state" / "profile-identity-registry.json").read_text(
            encoding="utf-8"
        )
    )
    assert next(iter(registry["bindings"].values()))["profile"] == profile
    assert not claim_path.exists()


@pytest.mark.asyncio
async def test_partial_create_is_quarantined_and_retry_converges(tmp_path):
    runner = _runner(tmp_path)
    first = _dm("ou_partial_create")

    from gateway.profile_provisioning import ProfileIdentityRegistry

    profile = ProfileIdentityRegistry.deterministic_profile_name(first)
    profile_dir = tmp_path / "profiles" / profile

    def partial_create(_profile, **_kwargs):
        profile_dir.mkdir(parents=True)
        (profile_dir / "partial.canary").write_text("partial", encoding="utf-8")
        raise OSError("create_profile crashed")

    with patch(
        "hermes_cli.profiles._get_default_hermes_home",
        return_value=tmp_path,
    ), patch(
        "hermes_cli.profiles.create_profile",
        side_effect=partial_create,
    ):
        assert await runner.prepare_inbound_source(first) is None

    assert profile_dir.is_dir()
    retry = _dm("ou_partial_create")
    with patch(
        "hermes_cli.profiles._get_default_hermes_home",
        return_value=tmp_path,
    ):
        assert await runner.prepare_inbound_source(retry) is retry

    quarantines = list((tmp_path / "profiles").glob(f".{profile}.partial-*"))
    assert len(quarantines) == 1
    assert (quarantines[0] / "partial.canary").read_text(encoding="utf-8") == "partial"
    assert retry.profile == profile
    _assert_valid_published_profile(tmp_path, profile)
    steady = _dm("ou_partial_create")
    with patch(
        "hermes_cli.profiles._get_default_hermes_home",
        return_value=tmp_path,
    ):
        assert await runner.prepare_inbound_source(steady) is steady
    assert steady.profile == profile
    assert len(list((tmp_path / "profiles").glob(f".{profile}.partial-*"))) == 1


@pytest.mark.asyncio
async def test_materialized_claim_write_failure_recovers_on_retry(tmp_path):
    runner = _runner(tmp_path)
    first = _dm("ou_materialized_claim_failure")

    from gateway.profile_provisioning import ProfileIdentityRegistry

    original_write = ProfileIdentityRegistry._atomic_write_json
    failed = False

    def fail_first_materialized_claim(path, data):
        nonlocal failed
        if (
            path.parent.name == "profile-provision-claims"
            and data.get("status") == "materialized"
            and not failed
        ):
            failed = True
            raise OSError("materialized claim write failed")
        return original_write(path, data)

    with patch(
        "hermes_cli.profiles._get_default_hermes_home",
        return_value=tmp_path,
    ), patch.object(
        ProfileIdentityRegistry,
        "_atomic_write_json",
        side_effect=fail_first_materialized_claim,
    ):
        assert await runner.prepare_inbound_source(first) is None

    profile = ProfileIdentityRegistry.deterministic_profile_name(first)
    profile_dir = tmp_path / "profiles" / profile
    assert profile_dir.is_dir()
    retry = _dm("ou_materialized_claim_failure")
    with patch(
        "hermes_cli.profiles._get_default_hermes_home",
        return_value=tmp_path,
    ):
        assert await runner.prepare_inbound_source(retry) is retry

    assert retry.profile == profile
    _assert_valid_published_profile(tmp_path, profile)
    assert len(list((tmp_path / "profiles").glob(f".{profile}.partial-*"))) == 1
    steady = _dm("ou_materialized_claim_failure")
    with patch(
        "hermes_cli.profiles._get_default_hermes_home",
        return_value=tmp_path,
    ):
        assert await runner.prepare_inbound_source(steady) is steady
    assert steady.profile == profile
    assert len(list((tmp_path / "profiles").glob(f".{profile}.partial-*"))) == 1


@pytest.mark.asyncio
async def test_missing_marker_does_not_adopt_unknown_preexisting_profile(tmp_path):
    runner = _runner(tmp_path)
    source = _dm("ou_unknown_profile")

    from gateway.profile_provisioning import ProfileIdentityRegistry

    profile = ProfileIdentityRegistry.deterministic_profile_name(source)
    profile_dir = tmp_path / "profiles" / profile
    profile_dir.mkdir(parents=True)
    (profile_dir / "foreign.txt").write_text("operator-owned", encoding="utf-8")

    with patch(
        "hermes_cli.profiles._get_default_hermes_home",
        return_value=tmp_path,
    ):
        assert await runner.prepare_inbound_source(source) is None

    assert source.profile is None
    assert source.profile_prepare_rejected == "profile_target_conflict"
    assert not (profile_dir / ProfileIdentityRegistry.MARKER_FILENAME).exists()
    assert not (tmp_path / "state" / "profile-identity-registry.json").exists()


@pytest.mark.asyncio
async def test_mismatched_creating_claim_does_not_adopt_existing_profile(tmp_path):
    runner = _runner(tmp_path)
    source = _dm("ou_mismatched_claim")

    from gateway.profile_provisioning import ProfileIdentityRegistry

    registry = ProfileIdentityRegistry(tmp_path)
    profile = registry.deterministic_profile_name(source)
    profile_dir = tmp_path / "profiles" / profile
    profile_dir.mkdir(parents=True)
    (profile_dir / "foreign.txt").write_text("operator-owned", encoding="utf-8")
    _key, digest, kind = registry.identity_for_source(source)
    claim_path = registry._claim_path(digest)
    registry._atomic_write_json(
        claim_path,
        {
            "schema_version": registry.CLAIM_VERSION,
            "platform": "feishu",
            "kind": kind,
            "identity_digest": "sha256:" + "0" * 64,
            "profile": profile,
            "status": "creating",
        },
    )

    with patch(
        "hermes_cli.profiles._get_default_hermes_home",
        return_value=tmp_path,
    ):
        assert await runner.prepare_inbound_source(source) is None

    assert source.profile_prepare_rejected == "profile_target_conflict"
    assert (profile_dir / "foreign.txt").is_file()
    assert not list((tmp_path / "profiles").glob(f".{profile}.partial-*"))
    assert not (tmp_path / "state" / "profile-identity-registry.json").exists()


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
