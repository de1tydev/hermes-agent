#!/usr/bin/env python3
"""为 TODE68 全部 Hermes Profile 配置 NewAPI 多模态亲和。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


SCHEMA = "tode68-newapi-affinity-config/v1"
VISION_MODEL = "deepseek-v4-flash-vision-exp"
VISION_PROVIDER = "tode"
NEWAPI_HOST = "newapi.tode.ltd"
_VISION_ENDPOINT_KEYS = ("base_url", "api_key", "key_env", "api_key_env")


class AffinityConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConfigUpdate:
    path: Path
    before: bytes
    after: bytes
    uid: int
    gid: int
    mode: int


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _atomic_write(
    path: Path,
    payload: bytes,
    *,
    uid: int,
    gid: int,
    mode: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_path)
    try:
        os.fchmod(fd, mode)
        if os.geteuid() == 0:
            os.fchown(fd, uid, gid)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _config_paths(root: Path) -> list[Path]:
    paths = [root / "config.yaml"]
    profiles = root / "profiles"
    if profiles.is_dir():
        paths.extend(sorted(profiles.glob("*/config.yaml")))
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise AffinityConfigError(f"regular config file required: {path}")
    return paths


def _updated_config(path: Path) -> ConfigUpdate:
    before = path.read_bytes()
    config = yaml.safe_load(before) or {}
    if not isinstance(config, dict):
        raise AffinityConfigError(f"config root is not an object: {path}")

    providers = config.get("providers")
    if not isinstance(providers, dict):
        raise AffinityConfigError(f"providers mapping missing: {path}")
    tode = providers.get(VISION_PROVIDER)
    if not isinstance(tode, dict):
        raise AffinityConfigError(f"providers.tode mapping missing: {path}")
    host = (urlparse(str(tode.get("base_url") or "")).hostname or "").lower()
    if host != NEWAPI_HOST:
        raise AffinityConfigError(f"providers.tode is not TODE NewAPI: {path}")

    models = tode.get("models")
    if not isinstance(models, dict):
        models = {}
        tode["models"] = models
    model_config = models.get(VISION_MODEL)
    if not isinstance(model_config, dict):
        model_config = {}
        models[VISION_MODEL] = model_config
    model_config.setdefault("context_length", 1_000_000)
    model_config["supports_vision"] = True

    auxiliary = config.get("auxiliary")
    if not isinstance(auxiliary, dict):
        auxiliary = {}
        config["auxiliary"] = auxiliary
    vision = auxiliary.get("vision")
    if not isinstance(vision, dict):
        vision = {}
        auxiliary["vision"] = vision
    vision["provider"] = VISION_PROVIDER
    vision["model"] = VISION_MODEL
    for key in _VISION_ENDPOINT_KEYS:
        vision.pop(key, None)

    after = yaml.safe_dump(
        config,
        allow_unicode=True,
        sort_keys=False,
    ).encode("utf-8")
    stat_result = path.stat()
    return ConfigUpdate(
        path=path,
        before=before,
        after=after,
        uid=stat_result.st_uid,
        gid=stat_result.st_gid,
        mode=stat_result.st_mode & 0o777,
    )


def _verify(path: Path) -> None:
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    vision = (config.get("auxiliary") or {}).get("vision") or {}
    if vision.get("provider") != VISION_PROVIDER or vision.get("model") != VISION_MODEL:
        raise AffinityConfigError(f"vision configuration verification failed: {path}")
    if any(key in vision for key in _VISION_ENDPOINT_KEYS):
        raise AffinityConfigError(f"vision endpoint override survived: {path}")
    model_config = (
        ((config.get("providers") or {}).get(VISION_PROVIDER) or {}).get("models") or {}
    ).get(VISION_MODEL) or {}
    if model_config.get("supports_vision") is not True:
        raise AffinityConfigError(
            f"vision model capability verification failed: {path}"
        )


def configure(
    root: Path,
    *,
    apply: bool,
    stamp: str | None = None,
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    updates = [_updated_config(path) for path in _config_paths(root)]
    changed = [update for update in updates if update.before != update.after]
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = root / "backups" / f"newapi-affinity-config-{stamp}"
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA,
        "applied": apply,
        "config_count": len(updates),
        "changed_count": len(changed),
        "verified_count": 0,
        "vision_provider": VISION_PROVIDER,
        "vision_model": VISION_MODEL,
        "backup": str(backup),
        "files": [
            {
                "path": str(update.path.relative_to(root)),
                "before_sha256": _sha256(update.before),
                "after_sha256": _sha256(update.after),
                "changed": update.before != update.after,
            }
            for update in updates
        ],
    }
    if not apply:
        return receipt

    root_stat = (root / "config.yaml").stat()
    backup.mkdir(parents=True, mode=0o700)
    backup.chmod(0o700)
    if os.geteuid() == 0:
        os.chown(backup, root_stat.st_uid, root_stat.st_gid)
    for update in updates:
        relative = update.path.relative_to(root)
        backup_path = backup / relative
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(update.path, backup_path)
        if os.geteuid() == 0:
            os.chown(backup_path, update.uid, update.gid)

    written: list[ConfigUpdate] = []
    try:
        for update in changed:
            _atomic_write(
                update.path,
                update.after,
                uid=update.uid,
                gid=update.gid,
                mode=update.mode,
            )
            written.append(update)
        for update in updates:
            _verify(update.path)
        receipt["verified_count"] = len(updates)
    except Exception:
        for update in reversed(written):
            _atomic_write(
                update.path,
                update.before,
                uid=update.uid,
                gid=update.gid,
                mode=update.mode,
            )
        raise

    receipt_path = root / "migration" / "newapi-affinity-config-receipt.json"
    receipt_payload = (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_write(
        receipt_path,
        receipt_payload,
        uid=root_stat.st_uid,
        gid=root_stat.st_gid,
        mode=0o600,
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(
        description="配置 TODE68 Hermes NewAPI 图片亲和与专用多模态模型。"
    )
    parser.add_argument("--root", type=Path, default=Path("/opt/data"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    receipt = configure(args.root, apply=args.apply)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
