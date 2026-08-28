"""Authorized identity-to-profile bindings for multiplex gateway ingress.

The registry stores only stable identity digests, never raw platform IDs.  It
is rooted under the primary gateway profile and published atomically while a
process-safe file lock is held.  Profile creation deliberately reuses the
normal CLI materializer so auto-created profiles have the same on-disk shape
as operator-created profiles.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def _lock_file(handle: Any) -> None:
    if os.name == "nt":
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write("0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


class ProfileProvisionRejected(RuntimeError):
    """Fail-closed profile preparation error with a stable machine code."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class ProfileIdentityRegistry:
    """Resolve or provision one profile for a stable inbound identity."""

    SCHEMA_VERSION = "hermes-profile-identity-registry/v1"
    MARKER_VERSION = "hermes-auto-profile/v1"
    REGISTRY_RELATIVE_PATH = Path("state/profile-identity-registry.json")
    LOCK_RELATIVE_PATH = Path("state/profile-identity-registry.lock")
    MARKER_FILENAME = ".hermes-auto-profile.json"

    def __init__(self, primary_home: Path) -> None:
        self.primary_home = Path(primary_home)
        self.registry_path = self.primary_home / self.REGISTRY_RELATIVE_PATH
        self.lock_path = self.primary_home / self.LOCK_RELATIVE_PATH

    @staticmethod
    def identity_for_source(source: Any) -> tuple[str, str, str]:
        platform_obj = getattr(source, "platform", None)
        platform = str(getattr(platform_obj, "value", platform_obj) or "").strip().lower()
        if not platform:
            raise ProfileProvisionRejected(
                "profile_identity_missing", "inbound platform identity is missing"
            )

        chat_type = str(getattr(source, "chat_type", "") or "").strip().lower()
        if chat_type == "dm":
            kind = "dm"
            identity = str(getattr(source, "user_id", "") or "").strip()
        else:
            kind = "group"
            identity = str(getattr(source, "chat_id", "") or "").strip()
        if not identity:
            raise ProfileProvisionRejected(
                "profile_identity_missing",
                f"{platform} {kind} identity is missing",
            )

        digest = hashlib.sha256(
            f"{platform}\0{kind}\0{identity}".encode("utf-8")
        ).hexdigest()
        return f"{platform}:{kind}:sha256:{digest}", f"sha256:{digest}", kind

    @staticmethod
    def deterministic_profile_name(source: Any) -> str:
        key, digest, kind = ProfileIdentityRegistry.identity_for_source(source)
        platform = key.split(":", 1)[0]
        return f"{platform}-{kind}-{digest.removeprefix('sha256:')[:16]}"

    def lookup(self, source: Any) -> Optional[str]:
        key, digest, kind = self.identity_for_source(source)
        data = self._read_registry()
        binding = data["bindings"].get(key)
        if binding is None:
            return None
        return self._validate_binding(binding, source, digest, kind)

    def resolve_or_provision(
        self,
        source: Any,
        *,
        static_profile: Optional[str],
        profile_allowlist: Optional[list[str]],
    ) -> str:
        """Return the binding for ``source``, creating it when unknown.

        ``static_profile`` wins only when no dynamic binding exists.  A
        disagreement is a configuration conflict rather than a default-profile
        fallback.  The caller must run authorization before entering here.
        """
        key, digest, kind = self.identity_for_source(source)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.chmod(self.lock_path, 0o600)
            with os.fdopen(lock_fd, "r+") as lock_file:
                lock_fd = -1
                _lock_file(lock_file)
                data = self._read_registry()
                binding = data["bindings"].get(key)
                dynamic_profile = (
                    self._validate_binding(binding, source, digest, kind)
                    if binding is not None
                    else None
                )
                if static_profile:
                    if dynamic_profile and dynamic_profile != static_profile:
                        raise ProfileProvisionRejected(
                            "profile_route_conflict",
                            "static and dynamic identity routes disagree",
                        )
                    self._require_served(static_profile, profile_allowlist)
                    return static_profile
                if dynamic_profile:
                    self._require_existing_and_served(
                        dynamic_profile, profile_allowlist
                    )
                    return dynamic_profile

                profile = self.deterministic_profile_name(source)
                self._require_served(profile, profile_allowlist)
                self._materialize_profile(profile, digest, kind, source)
                data["bindings"][key] = {
                    "platform": str(getattr(source.platform, "value", source.platform)),
                    "kind": kind,
                    "identity_digest": digest,
                    "profile": profile,
                }
                self._write_registry(data)
                return profile
        finally:
            if lock_fd >= 0:
                os.close(lock_fd)

    def _read_registry(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            return {"schema_version": self.SCHEMA_VERSION, "bindings": {}}
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ProfileProvisionRejected(
                "profile_registry_invalid", "identity registry is unreadable"
            ) from exc
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != self.SCHEMA_VERSION
            or not isinstance(data.get("bindings"), dict)
        ):
            raise ProfileProvisionRejected(
                "profile_registry_invalid", "identity registry schema is invalid"
            )
        return data

    def _validate_binding(
        self,
        binding: Any,
        source: Any,
        digest: str,
        kind: str,
    ) -> str:
        platform = str(getattr(source.platform, "value", source.platform))
        if not isinstance(binding, dict):
            raise ProfileProvisionRejected(
                "profile_registry_invalid", "identity binding is not an object"
            )
        if (
            binding.get("platform") != platform
            or binding.get("kind") != kind
            or binding.get("identity_digest") != digest
        ):
            raise ProfileProvisionRejected(
                "profile_registry_conflict", "identity binding metadata conflicts"
            )
        profile = binding.get("profile")
        if not isinstance(profile, str) or not profile.strip():
            raise ProfileProvisionRejected(
                "profile_registry_invalid", "identity binding profile is invalid"
            )
        from hermes_cli.profiles import normalize_profile_name, validate_profile_name

        profile = normalize_profile_name(profile)
        validate_profile_name(profile)
        return profile

    @staticmethod
    def _require_served(
        profile: str, profile_allowlist: Optional[list[str]]
    ) -> None:
        if profile == "default" or profile_allowlist is None:
            return
        from hermes_cli.profiles import normalize_profile_name

        allowed = {
            normalize_profile_name(value)
            for value in profile_allowlist
            if isinstance(value, str) and value.strip()
        }
        if profile not in allowed:
            raise ProfileProvisionRejected(
                "profile_not_served",
                f"profile {profile!r} is outside the multiplex allowlist",
            )

    def _require_existing_and_served(
        self, profile: str, profile_allowlist: Optional[list[str]]
    ) -> None:
        self._require_served(profile, profile_allowlist)
        from hermes_cli.profiles import profile_exists

        if not profile_exists(profile):
            raise ProfileProvisionRejected(
                "profile_target_missing", f"bound profile {profile!r} is missing"
            )

    def _materialize_profile(
        self, profile: str, digest: str, kind: str, source: Any
    ) -> None:
        from hermes_cli.profiles import create_profile, get_profile_dir, profile_exists

        profile_dir = get_profile_dir(profile)
        marker_path = profile_dir / self.MARKER_FILENAME
        marker = {
            "schema_version": self.MARKER_VERSION,
            "platform": str(getattr(source.platform, "value", source.platform)),
            "kind": kind,
            "identity_digest": digest,
            "profile": profile,
        }
        if profile_exists(profile):
            try:
                existing = json.loads(marker_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ProfileProvisionRejected(
                    "profile_target_conflict",
                    f"deterministic profile {profile!r} already exists",
                ) from exc
            if existing != marker:
                raise ProfileProvisionRejected(
                    "profile_target_conflict",
                    f"deterministic profile {profile!r} belongs to another identity",
                )
            return

        try:
            create_profile(profile, no_alias=True)
            self._atomic_write_json(marker_path, marker)
        except ProfileProvisionRejected:
            raise
        except Exception as exc:
            raise ProfileProvisionRejected(
                "profile_create_failed", f"could not create profile {profile!r}"
            ) from exc

    def _write_registry(self, data: dict[str, Any]) -> None:
        self._atomic_write_json(self.registry_path, data)

    @staticmethod
    def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        payload = (json.dumps(data, sort_keys=True, indent=2) + "\n").encode("utf-8")
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                fd = -1
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, path)
            os.chmod(path, 0o600)
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
