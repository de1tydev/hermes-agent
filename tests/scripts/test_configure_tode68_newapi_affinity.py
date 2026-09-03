import json
from pathlib import Path

import yaml

from scripts.configure_tode68_newapi_affinity import (
    VISION_MODEL,
    configure,
)


def _write_config(path: Path, *, stale_vision_endpoint: bool = False) -> bytes:
    data = {
        "model": {"provider": "tode", "default": "deepseek-v4-flash"},
        "providers": {
            "tode": {
                "name": "TODE NewAPI",
                "base_url": "https://newapi.tode.ltd/v1",
                "key_env": "OPENAI_API_KEY",
                "models": {
                    "deepseek-v4-flash": {"context_length": 1_000_000},
                },
            }
        },
        "auxiliary": {
            "vision": {
                "timeout": 180,
                **(
                    {
                        "base_url": "https://stale.example/v1",
                        "api_key": "must-not-survive",
                    }
                    if stale_vision_endpoint
                    else {}
                ),
            }
        },
    }
    payload = yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)
    return payload


def test_configure_updates_root_and_profiles_with_recoverable_backups(tmp_path):
    root_before = _write_config(tmp_path / "config.yaml")
    profile_before = _write_config(
        tmp_path / "profiles" / "feishu-dm-test" / "config.yaml",
        stale_vision_endpoint=True,
    )

    receipt = configure(
        tmp_path,
        apply=True,
        stamp="20260903-120000",
    )

    assert receipt["changed_count"] == 2
    assert receipt["verified_count"] == 2
    assert receipt["vision_provider"] == "tode"
    assert receipt["vision_model"] == VISION_MODEL

    for path in (
        tmp_path / "config.yaml",
        tmp_path / "profiles" / "feishu-dm-test" / "config.yaml",
    ):
        config = yaml.safe_load(path.read_text())
        vision = config["auxiliary"]["vision"]
        assert vision["provider"] == "tode"
        assert vision["model"] == VISION_MODEL
        assert vision["timeout"] == 180
        assert "base_url" not in vision
        assert "api_key" not in vision
        assert config["providers"]["tode"]["models"][VISION_MODEL] == {
            "context_length": 1_000_000,
            "supports_vision": True,
        }
        assert path.stat().st_mode & 0o777 == 0o600

    backup = Path(receipt["backup"])
    assert (backup / "config.yaml").read_bytes() == root_before
    assert (
        backup / "profiles" / "feishu-dm-test" / "config.yaml"
    ).read_bytes() == profile_before

    receipt_path = tmp_path / "migration" / "newapi-affinity-config-receipt.json"
    assert json.loads(receipt_path.read_text())["changed_count"] == 2


def test_dry_run_does_not_modify_configs(tmp_path):
    before = _write_config(tmp_path / "config.yaml")

    receipt = configure(tmp_path, apply=False, stamp="20260903-120000")

    assert receipt["changed_count"] == 1
    assert receipt["applied"] is False
    assert (tmp_path / "config.yaml").read_bytes() == before
    assert not (tmp_path / "backups").exists()
