"""Preview and materialize an OpenClaw multi-agent inventory.

The manifest deliberately contains identity digests and source metadata only.
It never copies workspace content, sessions, credentials, media, or indexes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4


MANIFEST_SCHEMA = "hermes-openclaw-multi-agent-manifest/v1"
REGISTRY_SCHEMA = "hermes-profile-identity-registry/v1"
USER_REGISTRY_SCHEMA = "openclaw-automation-user-registry/v1"
GROUP_REGISTRY_SCHEMA = "openclaw-automation-group-registry/v1"
PROFILE_MARKER_SCHEMA = "hermes-openclaw-profile/v1"
WORKSPACE_MARKER_SCHEMA = "hermes-openclaw-workspace/v1"

_PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_DENIED_PARTS = frozenset(
    {
        "auth",
        "auth-profile",
        "auth-profiles",
        "auth_profiles",
        "backup",
        "backups",
        "build",
        "cache",
        "checkpoint",
        "checkpoints",
        "deleted",
        "log",
        "logs",
        "media",
        "node_modules",
        "reset",
        "resets",
        "secret",
        "secrets",
        "session",
        "sessions",
        "state-snapshots",
        "trajectory",
        "trajectories",
        "__pycache__",
    }
)
_DENIED_SUFFIXES = (
    ".db",
    ".db-shm",
    ".db-wal",
    ".env",
    ".log",
    ".sqlite",
    ".sqlite3",
    ".idx",
    ".index",
)
_MEDIA_SUFFIXES = (
    ".avi",
    ".gif",
    ".jpeg",
    ".jpg",
    ".m4a",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".png",
    ".svg",
    ".wav",
    ".webm",
    ".webp",
)
_SECRET_CONTENT = re.compile(
    rb"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]|"
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    rb"\b(?:sk|xox[baprs]|gh[pousr])-[A-Za-z0-9_-]{8,}"
)
_PROFILE_DIRS = (
    "memories",
    "sessions",
    "skills",
    "skins",
    "logs",
    "plans",
    "workspace",
    "cron",
    "home",
)


class MultiAgentMigrationError(RuntimeError):
    """Fail-closed migration error carrying a stable machine code."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Return the immutable, byte-stable representation used on disk."""
    return (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MultiAgentMigrationError(code, path.name) from exc
    if not isinstance(value, dict):
        raise MultiAgentMigrationError(code, f"{path.name} is not an object")
    return value


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _identity_digest(kind: str, identity: str) -> str:
    digest = hashlib.sha256(f"feishu\0{kind}\0{identity}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _profile_for_identity(kind: str, digest: str) -> str:
    return f"feishu-{kind}-{digest.removeprefix('sha256:')[:16]}"


def _denied_path(relative_path: str) -> bool:
    path = Path(relative_path)
    lowered = [part.lower().lstrip(".") for part in path.parts]
    name = lowered[-1]
    raw_name = path.name.lower()
    denied_name = name.startswith(
        ("auth-profile", "checkpoint", "secret", "session", "trajectory")
    )
    return (
        any(part in _DENIED_PARTS for part in lowered)
        or denied_name
        or raw_name.startswith(".env")
        or name in {"auth.json", "dist", "site-packages", "venv"}
        or name.endswith((*_DENIED_SUFFIXES, *_MEDIA_SUFFIXES))
    )


class MultiAgentMigration:
    """Build an immutable plan and apply it to one Hermes home."""

    def __init__(self, source_root: Path, target_root: Path) -> None:
        self.source_root = Path(source_root).resolve()
        self.target_root = Path(target_root).resolve()

    def preview(self) -> dict[str, Any]:
        """Build a side-effect-free multi-agent migration manifest."""
        config_path = self.source_root / "openclaw.json"
        users_path = self.source_root / "automation/users.json"
        groups_path = self.source_root / "automation/groups.json"
        if not self.source_root.is_dir():
            raise MultiAgentMigrationError("source_missing")
        config = _load_json(config_path, "source_config_invalid")
        user_entries = self._load_registry(
            users_path, USER_REGISTRY_SCHEMA, "user_registry_invalid"
        )
        group_entries = self._load_registry(
            groups_path, GROUP_REGISTRY_SCHEMA, "group_registry_invalid"
        )

        agents_value = config.get("agents")
        agents = agents_value.get("list") if isinstance(agents_value, dict) else None
        bindings = config.get("bindings")
        if not isinstance(agents, list) or not isinstance(bindings, list):
            raise MultiAgentMigrationError(
                "inventory_invalid", "agents.list and bindings must be arrays"
            )

        identities = self._identity_index(user_entries, group_entries)
        profile_records, workspace_roots = self._profile_records(agents, identities)
        binding_records = self._binding_records(bindings, identities, profile_records)
        source_refs = self._classify_source_refs(workspace_roots)

        snapshot_inputs = {
            "config_sha256": _sha256_file(config_path),
            "groups_sha256": _sha256_file(groups_path),
            "source_refs": source_refs,
            "users_sha256": _sha256_file(users_path),
        }
        snapshot_hex = _sha256_bytes(canonical_manifest_bytes(snapshot_inputs))
        review_items = [
            {
                "code": "binding_requires_review",
                "record_id": item["record_id"],
                "reason": item["reason"],
            }
            for item in binding_records
            if item["action"] == "review"
        ]
        target_conflicts = self._preview_target_conflicts(
            profile_records, binding_records, f"sha256:{snapshot_hex}"
        )
        planned_writes: list[dict[str, str]] = []
        for record in profile_records:
            planned_writes.extend(
                [
                    {"action": "ensure-directory", "target": record["target_profile_root"]},
                    {"action": "ensure-directory", "target": record["target_workspace_root"]},
                    {
                        "action": "write-profile-marker",
                        "target": f"{record['target_profile_root']}/.hermes-openclaw-profile.json",
                    },
                    {
                        "action": "write-workspace-marker",
                        "target": f"{record['target_workspace_root']}/.hermes-openclaw-workspace.json",
                    },
                ]
            )
        planned_writes.extend(
            [
                {
                    "action": "merge-registry-v1",
                    "target": "state/profile-identity-registry.json",
                },
                {
                    "action": "write-immutable-manifest",
                    "target": "migration/openclaw/multi-agent-manifest.json",
                },
                {
                    "action": "create-restore-point",
                    "target": f"migration/openclaw/restore-points/{snapshot_hex}",
                },
            ]
        )

        counts = {
            "bindings": len(binding_records),
            "functional_profiles": sum(
                record["profile_kind"] == "functional" for record in profile_records
            ),
            "group_profiles": sum(
                record["profile_kind"] == "group" for record in profile_records
            ),
            "profiles": len(profile_records),
            "review_bindings": len(review_items),
            "user_profiles": sum(
                record["profile_kind"] == "dm" for record in profile_records
            ),
        }
        return {
            "binding_records": binding_records,
            "classified_source_refs": source_refs,
            "conflicts": target_conflicts,
            "counts": counts,
            "planned_target_refs": sorted(
                {item["target"] for item in planned_writes}
            ),
            "planned_writes": planned_writes,
            "profile_records": profile_records,
            "review_items": review_items,
            "schema_version": MANIFEST_SCHEMA,
            "source_snapshot": {
                "id": f"sha256:{snapshot_hex}",
                "inputs": snapshot_inputs,
            },
        }

    @staticmethod
    def _load_registry(
        path: Path, expected_schema: str, error_code: str
    ) -> list[dict[str, str]]:
        data = _load_json(path, error_code)
        entries = data.get("entries")
        if data.get("schema_version") != expected_schema or not isinstance(entries, list):
            raise MultiAgentMigrationError(error_code, "schema mismatch")
        normalized: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise MultiAgentMigrationError(error_code, "entry is not an object")
            agent_id = entry.get("agent_id")
            identity = entry.get("identity")
            if not isinstance(agent_id, str) or not isinstance(identity, str):
                raise MultiAgentMigrationError(error_code, "entry identity is invalid")
            if not agent_id.strip() or not identity.strip():
                raise MultiAgentMigrationError(error_code, "entry identity is empty")
            normalized.append({"agent_id": agent_id.strip(), "identity": identity.strip()})
        return normalized

    @staticmethod
    def _identity_index(
        users: list[dict[str, str]], groups: list[dict[str, str]]
    ) -> dict[str, tuple[str, str]]:
        result: dict[str, tuple[str, str]] = {}
        seen_identities: set[tuple[str, str]] = set()
        for kind, entries in (("dm", users), ("group", groups)):
            for entry in entries:
                agent_id = entry["agent_id"]
                identity = entry["identity"]
                if agent_id in result or (kind, identity) in seen_identities:
                    raise MultiAgentMigrationError(
                        "identity_conflict", "registry entries are not one-to-one"
                    )
                result[agent_id] = (kind, identity)
                seen_identities.add((kind, identity))
        return result

    def _profile_records(
        self,
        agents: list[Any],
        identities: dict[str, tuple[str, str]],
    ) -> tuple[list[dict[str, str]], dict[str, Path]]:
        records: list[dict[str, str]] = []
        workspace_roots: dict[str, Path] = {}
        seen_agents: set[str] = set()
        seen_profiles: set[str] = set()
        for value in agents:
            if not isinstance(value, dict):
                raise MultiAgentMigrationError("agent_invalid")
            agent_id = value.get("id")
            workspace = value.get("workspace")
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise MultiAgentMigrationError("agent_invalid", "agent id is missing")
            agent_id = agent_id.strip()
            if agent_id in seen_agents:
                raise MultiAgentMigrationError("agent_conflict", "duplicate agent id")
            seen_agents.add(agent_id)
            if not isinstance(workspace, str) or not workspace.strip():
                raise MultiAgentMigrationError("workspace_missing", agent_id)
            source_workspace = self._resolve_workspace(workspace)
            workspace_roots[agent_id] = source_workspace

            identity = identities.get(agent_id)
            if identity is None:
                profile_kind = "functional"
                profile_id = agent_id.lower()
                if not _PROFILE_RE.fullmatch(profile_id):
                    raise MultiAgentMigrationError(
                        "profile_id_invalid", "functional agent id is not profile-safe"
                    )
            else:
                profile_kind, raw_identity = identity
                profile_id = _profile_for_identity(
                    profile_kind, _identity_digest(profile_kind, raw_identity)
                )
            if profile_id in seen_profiles:
                raise MultiAgentMigrationError("profile_conflict", "profile id collision")
            seen_profiles.add(profile_id)
            source_ref = _relative(source_workspace, self.source_root)
            records.append(
                {
                    "agent_id": agent_id,
                    "profile_id": profile_id,
                    "profile_kind": profile_kind,
                    "source_workspace_ref": source_ref,
                    "target_profile_root": f"profiles/{profile_id}",
                    "target_workspace_root": f"profiles/{profile_id}/workspace",
                }
            )
        missing_agents = sorted(set(identities) - seen_agents)
        if missing_agents:
            raise MultiAgentMigrationError(
                "registry_agent_missing", "registry references an unknown agent"
            )
        return sorted(records, key=lambda item: item["profile_id"]), workspace_roots

    def _resolve_workspace(self, value: str) -> Path:
        raw = Path(value).expanduser()
        if raw.is_absolute():
            unresolved = raw
        else:
            if ".." in raw.parts:
                raise MultiAgentMigrationError("workspace_outside_source")
            unresolved = self.source_root / raw
        try:
            relative_unresolved = unresolved.relative_to(self.source_root)
        except ValueError as exc:
            raise MultiAgentMigrationError("workspace_outside_source") from exc
        probe = self.source_root
        for part in relative_unresolved.parts:
            probe = probe / part
            if probe.is_symlink():
                raise MultiAgentMigrationError("workspace_symlink", value)
        candidate = unresolved.resolve()
        try:
            candidate.relative_to(self.source_root)
        except ValueError as exc:
            raise MultiAgentMigrationError("workspace_outside_source") from exc
        if not candidate.is_dir() or candidate.is_symlink():
            raise MultiAgentMigrationError("workspace_missing", value)
        return candidate

    @staticmethod
    def _binding_kind(peer_kind: Any) -> str | None:
        value = str(peer_kind or "").strip().lower()
        if value in {"direct", "dm", "user"}:
            return "dm"
        if value in {"chat", "group"}:
            return "group"
        return None

    def _binding_records(
        self,
        bindings: list[Any],
        identities: dict[str, tuple[str, str]],
        profiles: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        profile_by_agent = {record["agent_id"]: record["profile_id"] for record in profiles}
        seen: dict[str, str] = {}
        records: list[dict[str, str]] = []
        for index, binding in enumerate(bindings):
            if not isinstance(binding, dict):
                raise MultiAgentMigrationError("binding_invalid")
            agent_id = binding.get("agentId")
            match = binding.get("match")
            if not isinstance(agent_id, str) or agent_id not in profile_by_agent:
                raise MultiAgentMigrationError("binding_agent_missing")
            if not isinstance(match, dict):
                raise MultiAgentMigrationError("binding_invalid")
            channel = str(match.get("channel") or "").strip().lower()
            if channel != "feishu":
                raise MultiAgentMigrationError("binding_channel_unsupported")
            peer = match.get("peer")
            if not isinstance(peer, dict):
                records.append(
                    {
                        "action": "review",
                        "profile": profile_by_agent[agent_id],
                        "reason": "channel-wide binding is outside registry v1 identity routes",
                        "record_id": f"binding-{index:03d}",
                    }
                )
                continue
            kind = self._binding_kind(peer.get("kind"))
            raw_identity = peer.get("id")
            if kind is None or not isinstance(raw_identity, str) or not raw_identity.strip():
                raise MultiAgentMigrationError("binding_invalid")
            raw_identity = raw_identity.strip()
            digest = _identity_digest(kind, raw_identity)
            registry_key = f"feishu:{kind}:{digest}"
            profile = profile_by_agent[agent_id]
            previous = seen.get(registry_key)
            if previous is not None and previous != profile:
                raise MultiAgentMigrationError(
                    "binding_conflict", "one identity maps to multiple profiles"
                )
            if previous is not None:
                raise MultiAgentMigrationError("binding_duplicate")
            expected = identities.get(agent_id)
            if expected != (kind, raw_identity):
                raise MultiAgentMigrationError(
                    "binding_registry_conflict", "binding and automation registry disagree"
                )
            seen[registry_key] = profile
            records.append(
                {
                    "action": "materialize",
                    "identity_digest": digest,
                    "kind": kind,
                    "platform": "feishu",
                    "profile": profile,
                    "record_id": f"binding-{index:03d}",
                    "registry_key": registry_key,
                }
            )
        return records

    def _classify_source_refs(self, workspaces: dict[str, Path]) -> list[dict[str, str]]:
        refs: dict[str, dict[str, str]] = {}
        for workspace in sorted(set(workspaces.values()), key=str):
            self._walk_workspace(workspace, refs)
        return [refs[key] for key in sorted(refs)]

    def _walk_workspace(
        self, directory: Path, refs: dict[str, dict[str, str]]
    ) -> None:
        try:
            with os.scandir(directory) as scanner:
                entries = sorted(scanner, key=lambda item: item.name)
        except OSError as exc:
            raise MultiAgentMigrationError("workspace_unreadable") from exc
        for entry in entries:
            path = Path(entry.path)
            relative_path = _relative(path, self.source_root)
            if entry.is_symlink():
                refs[relative_path] = {
                    "action": "review",
                    "classification": "symlink",
                    "relative_path": relative_path,
                }
                continue
            if _denied_path(relative_path):
                refs[relative_path] = {
                    "action": "exclude",
                    "classification": "denied-path",
                    "relative_path": relative_path,
                }
                continue
            if entry.is_dir(follow_symlinks=False):
                self._walk_workspace(path, refs)
                continue
            if not entry.is_file(follow_symlinks=False):
                refs[relative_path] = {
                    "action": "review",
                    "classification": "special-file",
                    "relative_path": relative_path,
                }
                continue
            size = entry.stat(follow_symlinks=False).st_size
            if size > 1024 * 1024:
                refs[relative_path] = {
                    "action": "review",
                    "classification": "large-file",
                    "relative_path": relative_path,
                    "size": str(size),
                }
                continue
            payload = path.read_bytes()
            if _SECRET_CONTENT.search(payload):
                classification = "secret-content"
                action = "exclude"
            elif b"\0" in payload:
                classification = "binary"
                action = "review"
            else:
                classification = "durable-document"
                action = "classify-downstream"
            refs[relative_path] = {
                "action": action,
                "classification": classification,
                "relative_path": relative_path,
                "sha256": _sha256_bytes(payload),
                "size": str(size),
            }

    def _preview_target_conflicts(
        self,
        profiles: list[dict[str, str]],
        bindings: list[dict[str, str]],
        source_snapshot: str,
    ) -> list[dict[str, str]]:
        conflicts: list[dict[str, str]] = []
        for profile in profiles:
            root = self.target_root / profile["target_profile_root"]
            if not root.exists() and not root.is_symlink():
                continue
            marker_path = root / ".hermes-openclaw-profile.json"
            if root.is_symlink() or marker_path.is_symlink():
                marker = None
            else:
                try:
                    marker = json.loads(marker_path.read_text(encoding="utf-8"))
                except Exception:
                    marker = None
            if (
                not isinstance(marker, dict)
                or marker.get("schema_version") != PROFILE_MARKER_SCHEMA
                or marker.get("profile") != profile["profile_id"]
                or marker.get("source_snapshot") != source_snapshot
                or marker.get("source_workspace_ref")
                != profile["source_workspace_ref"]
                or not (root / "workspace").is_dir()
                or (root / "workspace").is_symlink()
            ):
                conflicts.append(
                    {
                        "code": "target_profile_conflict",
                        "target": profile["target_profile_root"],
                    }
                )

        registry_path = self.target_root / "state/profile-identity-registry.json"
        if registry_path.is_symlink():
            conflicts.append(
                {
                    "code": "target_registry_invalid",
                    "target": "state/profile-identity-registry.json",
                }
            )
        elif registry_path.exists():
            try:
                registry = json.loads(registry_path.read_text(encoding="utf-8"))
            except Exception:
                registry = None
            if (
                not isinstance(registry, dict)
                or registry.get("schema_version") != REGISTRY_SCHEMA
                or not isinstance(registry.get("bindings"), dict)
            ):
                conflicts.append(
                    {
                        "code": "target_registry_invalid",
                        "target": "state/profile-identity-registry.json",
                    }
                )
            else:
                existing_bindings = registry["bindings"]
                for record in bindings:
                    if record["action"] != "materialize":
                        continue
                    desired = {
                        "identity_digest": record["identity_digest"],
                        "kind": record["kind"],
                        "platform": record["platform"],
                        "profile": record["profile"],
                    }
                    existing = existing_bindings.get(record["registry_key"])
                    if existing is not None and existing != desired:
                        conflicts.append(
                            {
                                "code": "target_binding_conflict",
                                "record_id": record["record_id"],
                                "target": "state/profile-identity-registry.json",
                            }
                        )
        manifest_path = self.target_root / "migration/openclaw/multi-agent-manifest.json"
        if manifest_path.is_symlink():
            conflicts.append(
                {
                    "code": "target_manifest_invalid",
                    "target": "migration/openclaw/multi-agent-manifest.json",
                }
            )
        return sorted(
            conflicts,
            key=lambda item: (item["code"], item["target"], item.get("record_id", "")),
        )

    def apply(self, manifest: dict[str, Any]) -> dict[str, str]:
        """Apply one reviewed manifest, rolling back this call on failure."""
        if manifest.get("schema_version") != MANIFEST_SCHEMA:
            raise MultiAgentMigrationError("manifest_invalid")
        current = self.preview()
        if current["source_snapshot"]["id"] != manifest.get("source_snapshot", {}).get(
            "id"
        ):
            raise MultiAgentMigrationError("source_drift")
        if manifest.get("conflicts") or current["conflicts"]:
            raise MultiAgentMigrationError("target_conflict")

        desired_registry = self._desired_registry(manifest)
        self._preflight_targets(manifest, desired_registry)
        manifest_path = self.target_root / "migration/openclaw/multi-agent-manifest.json"
        registry_path = self.target_root / "state/profile-identity-registry.json"
        if manifest_path.is_symlink() or registry_path.is_symlink():
            raise MultiAgentMigrationError("target_conflict")
        snapshot_hex = manifest["source_snapshot"]["id"].removeprefix("sha256:")
        restore_point = (
            self.target_root / "migration/openclaw/restore-points" / snapshot_hex
        )
        if self._already_applied(manifest, desired_registry):
            return {
                "manifest": str(manifest_path),
                "restore_point": str(restore_point),
                "status": "unchanged",
            }

        target_existed = self.target_root.exists()
        registry_before = registry_path.read_bytes() if registry_path.exists() else None
        manifest_before = manifest_path.read_bytes() if manifest_path.exists() else None
        created_profiles: list[Path] = []
        restore_created = not restore_point.exists()
        try:
            self.target_root.mkdir(parents=True, exist_ok=True)
            self._create_restore_point(
                restore_point, registry_before, manifest_before, manifest
            )
            for profile in manifest["profile_records"]:
                root = self.target_root / profile["target_profile_root"]
                if root.exists():
                    continue
                self._materialize_profile(root, profile, manifest)
                created_profiles.append(root)
            self._atomic_write(registry_path, canonical_manifest_bytes(desired_registry))
            self._publish_manifest(manifest_path, manifest)
        except Exception as exc:
            for root in reversed(created_profiles):
                shutil.rmtree(root, ignore_errors=True)
            self._restore_file(registry_path, registry_before)
            self._restore_file(manifest_path, manifest_before)
            if restore_created:
                shutil.rmtree(restore_point, ignore_errors=True)
            self._prune_empty_target(target_existed)
            if isinstance(exc, MultiAgentMigrationError):
                raise
            raise MultiAgentMigrationError("apply_failed") from exc

        return {
            "manifest": str(manifest_path),
            "restore_point": str(restore_point),
            "status": "applied",
        }

    def _desired_registry(self, manifest: dict[str, Any]) -> dict[str, Any]:
        registry_path = self.target_root / "state/profile-identity-registry.json"
        if registry_path.is_symlink():
            raise MultiAgentMigrationError("target_registry_invalid")
        if registry_path.exists():
            data = _load_json(registry_path, "target_registry_invalid")
            if data.get("schema_version") != REGISTRY_SCHEMA or not isinstance(
                data.get("bindings"), dict
            ):
                raise MultiAgentMigrationError("target_registry_invalid")
        else:
            data = {"bindings": {}, "schema_version": REGISTRY_SCHEMA}
        bindings = dict(data["bindings"])
        for record in manifest["binding_records"]:
            if record["action"] != "materialize":
                continue
            desired = {
                "identity_digest": record["identity_digest"],
                "kind": record["kind"],
                "platform": record["platform"],
                "profile": record["profile"],
            }
            existing = bindings.get(record["registry_key"])
            if existing is not None and existing != desired:
                raise MultiAgentMigrationError("target_binding_conflict")
            bindings[record["registry_key"]] = desired
        return {"bindings": bindings, "schema_version": REGISTRY_SCHEMA}

    def _preflight_targets(
        self, manifest: dict[str, Any], desired_registry: dict[str, Any]
    ) -> None:
        del desired_registry
        for profile in manifest["profile_records"]:
            root = self.target_root / profile["target_profile_root"]
            if not root.exists() and not root.is_symlink():
                continue
            marker_path = root / ".hermes-openclaw-profile.json"
            expected = self._profile_marker(profile, manifest)
            if (
                root.is_symlink()
                or marker_path.is_symlink()
                or (root / "workspace").is_symlink()
            ):
                raise MultiAgentMigrationError("target_profile_conflict")
            try:
                existing = json.loads(marker_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise MultiAgentMigrationError("target_profile_conflict") from exc
            if existing != expected or not (root / "workspace").is_dir():
                raise MultiAgentMigrationError("target_profile_conflict")

    def _already_applied(
        self, manifest: dict[str, Any], desired_registry: dict[str, Any]
    ) -> bool:
        manifest_path = self.target_root / "migration/openclaw/multi-agent-manifest.json"
        registry_path = self.target_root / "state/profile-identity-registry.json"
        if not manifest_path.exists() or not registry_path.exists():
            return False
        if manifest_path.read_bytes() != canonical_manifest_bytes(manifest):
            return False
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if registry != desired_registry:
            return False
        return all(
            (self.target_root / profile["target_profile_root"]).is_dir()
            for profile in manifest["profile_records"]
        )

    @staticmethod
    def _profile_marker(
        profile: dict[str, str], manifest: dict[str, Any]
    ) -> dict[str, str]:
        return {
            "profile": profile["profile_id"],
            "schema_version": PROFILE_MARKER_SCHEMA,
            "source_snapshot": manifest["source_snapshot"]["id"],
            "source_workspace_ref": profile["source_workspace_ref"],
        }

    def _materialize_profile(
        self, root: Path, profile: dict[str, str], manifest: dict[str, Any]
    ) -> None:
        root.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent))
        try:
            for relative in _PROFILE_DIRS:
                (staging / relative).mkdir(mode=0o700)
            self._atomic_write(
                staging / ".hermes-openclaw-profile.json",
                canonical_manifest_bytes(self._profile_marker(profile, manifest)),
            )
            workspace_marker = {
                "schema_version": WORKSPACE_MARKER_SCHEMA,
                "source_snapshot": manifest["source_snapshot"]["id"],
                "source_workspace_ref": profile["source_workspace_ref"],
            }
            self._atomic_write(
                staging / "workspace/.hermes-openclaw-workspace.json",
                canonical_manifest_bytes(workspace_marker),
            )
            os.replace(staging, root)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _create_restore_point(
        self,
        root: Path,
        registry_before: bytes | None,
        manifest_before: bytes | None,
        manifest: dict[str, Any],
    ) -> None:
        if root.exists():
            return
        staging = root.with_name(f".{root.name}.{uuid4().hex}.tmp")
        staging.mkdir(parents=True, mode=0o700)
        try:
            if registry_before is not None:
                self._atomic_write(
                    staging / "profile-identity-registry.json", registry_before
                )
            if manifest_before is not None:
                self._atomic_write(
                    staging / "multi-agent-manifest.json", manifest_before
                )
            receipt = {
                "created_profile_roots": [
                    profile["target_profile_root"]
                    for profile in manifest["profile_records"]
                    if not (self.target_root / profile["target_profile_root"]).exists()
                ],
                "had_manifest": manifest_before is not None,
                "had_registry": registry_before is not None,
                "manifest_target": "migration/openclaw/multi-agent-manifest.json",
                "registry_target": "state/profile-identity-registry.json",
                "schema_version": "hermes-openclaw-restore-point/v1",
                "source_snapshot": manifest["source_snapshot"]["id"],
            }
            self._atomic_write(
                staging / "receipt.json", canonical_manifest_bytes(receipt)
            )
            root.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, root)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _publish_manifest(self, path: Path, manifest: dict[str, Any]) -> None:
        self._atomic_write(path, canonical_manifest_bytes(manifest))

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                fd = -1
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        finally:
            if fd >= 0:
                os.close(fd)
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _restore_file(path: Path, previous: bytes | None) -> None:
        if previous is None:
            path.unlink(missing_ok=True)
            return
        MultiAgentMigration._atomic_write(path, previous)

    def _prune_empty_target(self, target_existed: bool) -> None:
        for relative in (
            "migration/openclaw/restore-points",
            "migration/openclaw",
            "migration",
            "state",
            "profiles",
        ):
            path = self.target_root / relative
            try:
                path.rmdir()
            except OSError:
                pass
        if not target_existed:
            try:
                self.target_root.rmdir()
            except OSError:
                pass
