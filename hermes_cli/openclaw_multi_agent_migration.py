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
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4


MANIFEST_SCHEMA = "hermes-openclaw-multi-agent-manifest/v1"
REGISTRY_SCHEMA = "hermes-profile-identity-registry/v1"
USER_REGISTRY_SCHEMA = "openclaw-automation-user-registry/v1"
GROUP_REGISTRY_SCHEMA = "openclaw-automation-group-registry/v1"
PROFILE_MARKER_SCHEMA = "hermes-openclaw-profile/v1"
WORKSPACE_MARKER_SCHEMA = "hermes-openclaw-workspace/v1"
BINDING_RESOLUTIONS_SCHEMA = "hermes-openclaw-binding-resolutions/v1"
RESTORE_POINT_SCHEMA = "hermes-openclaw-restore-point/v1"
RETIRE_REASON = "channel_wide_route_replaced_by_identity_routes"

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
    rb"(?i)(?:api[_-]?key|access[_-]?token|bot[_-]?token|client[_-]?secret|"
    rb"password|authorization)[\"']?\s*[:=]\s*[\"']?(?:bearer\s+)?|"
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


def _file_identity(path: Path, root: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise MultiAgentMigrationError("source_file_invalid", path.name)
    stat_result = path.stat(follow_symlinks=False)
    return {
        "mtime_ns": stat_result.st_mtime_ns,
        "relative_path": _relative(path, root),
        "sha256": _sha256_file(path),
        "size": stat_result.st_size,
    }


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

    def __init__(
        self,
        source_root: Path,
        target_root: Path,
        *,
        binding_resolutions: dict[str, Any] | None = None,
    ) -> None:
        self.source_root = Path(source_root).resolve()
        self.target_root = Path(os.path.abspath(os.fspath(target_root)))
        self.binding_resolutions = self._normalize_binding_resolutions(
            binding_resolutions
        )

    @staticmethod
    def _normalize_binding_resolutions(
        value: dict[str, Any] | None,
    ) -> dict[str, dict[str, str]]:
        if value is None:
            return {}
        if not isinstance(value, dict) or set(value) != {
            "resolutions",
            "schema_version",
        }:
            raise MultiAgentMigrationError("binding_resolution_invalid")
        if value.get("schema_version") != BINDING_RESOLUTIONS_SCHEMA or not isinstance(
            value.get("resolutions"), list
        ):
            raise MultiAgentMigrationError("binding_resolution_invalid")
        result: dict[str, dict[str, str]] = {}
        expected_keys = {
            "outcome",
            "reason_code",
            "record_id",
            "source_binding_sha256",
        }
        for resolution in value["resolutions"]:
            if not isinstance(resolution, dict) or set(resolution) != expected_keys:
                raise MultiAgentMigrationError("binding_resolution_invalid")
            if (
                resolution.get("outcome") != "retire"
                or resolution.get("reason_code") != RETIRE_REASON
                or not isinstance(resolution.get("record_id"), str)
                or not re.fullmatch(
                    r"[0-9a-f]{64}", str(resolution.get("source_binding_sha256", ""))
                )
            ):
                raise MultiAgentMigrationError("binding_resolution_invalid")
            record_id = resolution["record_id"]
            if record_id in result:
                raise MultiAgentMigrationError("binding_resolution_invalid")
            result[record_id] = dict(resolution)
        return result

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
            "authoritative_sources": [
                _file_identity(path, self.source_root)
                for path in (config_path, users_path, groups_path)
            ],
            "source_refs": source_refs,
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
            "materialized_bindings": sum(
                record["action"] == "materialize" for record in binding_records
            ),
            "profiles": len(profile_records),
            "resolved_bindings": sum(
                record["action"] == "resolved" for record in binding_records
            ),
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
        consumed_resolutions: set[str] = set()
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
                record_id = f"binding-{index:03d}"
                source_binding_sha256 = _sha256_bytes(
                    canonical_manifest_bytes(binding)
                )
                resolution = self.binding_resolutions.get(record_id)
                if resolution is None:
                    records.append(
                        {
                            "action": "review",
                            "profile": profile_by_agent[agent_id],
                            "reason": "channel-wide binding is outside registry v1 identity routes",
                            "record_id": record_id,
                            "source_binding_sha256": source_binding_sha256,
                        }
                    )
                else:
                    if resolution["source_binding_sha256"] != source_binding_sha256:
                        raise MultiAgentMigrationError("binding_resolution_stale")
                    consumed_resolutions.add(record_id)
                    records.append(
                        {
                            "action": "resolved",
                            "outcome": resolution["outcome"],
                            "profile": profile_by_agent[agent_id],
                            "reason_code": resolution["reason_code"],
                            "record_id": record_id,
                            "source_binding_sha256": source_binding_sha256,
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
        if consumed_resolutions != set(self.binding_resolutions):
            raise MultiAgentMigrationError("binding_resolution_orphaned")
        return records

    def _classify_source_refs(self, workspaces: dict[str, Path]) -> list[dict[str, Any]]:
        refs: dict[str, dict[str, Any]] = {}
        for workspace in sorted(set(workspaces.values()), key=str):
            self._walk_workspace(workspace, refs)
        return [refs[key] for key in sorted(refs)]

    def _walk_workspace(
        self, directory: Path, refs: dict[str, dict[str, Any]]
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
            identity = _file_identity(path, self.source_root)
            size = identity["size"]
            if size > 1024 * 1024:
                refs[relative_path] = {
                    "action": "review",
                    "classification": "large-file",
                    **identity,
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
                **identity,
            }

    @staticmethod
    def _safe_relative_target(value: Any) -> PurePosixPath:
        if not isinstance(value, str) or not value:
            raise MultiAgentMigrationError("manifest_invalid")
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or not path.parts
            or ".." in path.parts
            or "." in path.parts
        ):
            raise MultiAgentMigrationError("manifest_invalid")
        return path

    def _has_symlink_component(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self.target_root)
        except ValueError as exc:
            raise MultiAgentMigrationError("target_outside_root") from exc
        probe = self.target_root
        if os.path.lexists(probe) and probe.is_symlink():
            return True
        for part in relative.parts:
            probe = probe / part
            if os.path.lexists(probe) and probe.is_symlink():
                return True
        return False

    @staticmethod
    def _workspace_marker(
        profile: dict[str, str], source_snapshot: str
    ) -> dict[str, str]:
        return {
            "schema_version": WORKSPACE_MARKER_SCHEMA,
            "source_snapshot": source_snapshot,
            "source_workspace_ref": profile["source_workspace_ref"],
        }

    def _owned_profile_is_valid(
        self, profile: dict[str, str], source_snapshot: str
    ) -> bool:
        root = self.target_root / profile["target_profile_root"]
        workspace = root / "workspace"
        profile_marker = root / ".hermes-openclaw-profile.json"
        workspace_marker = workspace / ".hermes-openclaw-workspace.json"
        controlled = [root, workspace, profile_marker, workspace_marker]
        controlled.extend(root / relative for relative in _PROFILE_DIRS)
        if any(self._has_symlink_component(path) for path in controlled):
            return False
        if not root.is_dir() or not workspace.is_dir():
            return False
        if any(not (root / relative).is_dir() for relative in _PROFILE_DIRS):
            return False
        try:
            actual_profile = json.loads(profile_marker.read_text(encoding="utf-8"))
            actual_workspace = json.loads(workspace_marker.read_text(encoding="utf-8"))
        except Exception:
            return False
        expected_profile = {
            "profile": profile["profile_id"],
            "schema_version": PROFILE_MARKER_SCHEMA,
            "source_snapshot": source_snapshot,
            "source_workspace_ref": profile["source_workspace_ref"],
        }
        return (
            actual_profile == expected_profile
            and actual_workspace == self._workspace_marker(profile, source_snapshot)
        )

    def _restore_point_is_valid(
        self,
        root: Path,
        profiles: list[dict[str, str]],
        source_snapshot: str,
    ) -> bool:
        if self._has_symlink_component(root) or not root.is_dir():
            return False
        receipt_path = root / "receipt.json"
        if self._has_symlink_component(receipt_path) or not receipt_path.is_file():
            return False
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        expected_keys = {
            "backup_sha256",
            "created_profile_roots",
            "had_manifest",
            "had_registry",
            "manifest_target",
            "registry_target",
            "schema_version",
            "source_snapshot",
        }
        if not isinstance(receipt, dict) or set(receipt) != expected_keys:
            return False
        if (
            receipt.get("schema_version") != RESTORE_POINT_SCHEMA
            or receipt.get("source_snapshot") != source_snapshot
            or receipt.get("manifest_target")
            != "migration/openclaw/multi-agent-manifest.json"
            or receipt.get("registry_target")
            != "state/profile-identity-registry.json"
            or not isinstance(receipt.get("created_profile_roots"), list)
            or not isinstance(receipt.get("backup_sha256"), dict)
        ):
            return False
        allowed_roots = {profile["target_profile_root"] for profile in profiles}
        created_roots = receipt["created_profile_roots"]
        if (
            len(created_roots) != len(set(created_roots))
            or not set(created_roots).issubset(allowed_roots)
        ):
            return False
        backup_sha = receipt["backup_sha256"]
        if set(backup_sha) != {"manifest", "registry"}:
            return False
        for label, had_key, filename in (
            ("manifest", "had_manifest", "multi-agent-manifest.json"),
            ("registry", "had_registry", "profile-identity-registry.json"),
        ):
            had_value = receipt.get(had_key)
            digest = backup_sha.get(label)
            backup = root / filename
            if not isinstance(had_value, bool):
                return False
            if had_value:
                if (
                    not isinstance(digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or self._has_symlink_component(backup)
                    or not backup.is_file()
                    or _sha256_file(backup) != digest
                ):
                    return False
            elif digest is not None or os.path.lexists(backup):
                return False
        allowed_entries = {
            "receipt.json",
            *(filename for filename in (
                "multi-agent-manifest.json",
                "profile-identity-registry.json",
            ) if (root / filename).exists()),
        }
        try:
            if {entry.name for entry in root.iterdir()} != allowed_entries:
                return False
        except OSError:
            return False
        return True

    def _preview_target_conflicts(
        self,
        profiles: list[dict[str, str]],
        bindings: list[dict[str, str]],
        source_snapshot: str,
    ) -> list[dict[str, str]]:
        conflicts: list[dict[str, str]] = []
        snapshot_hex = source_snapshot.removeprefix("sha256:")
        controlled_ancestors = [
            self.target_root,
            self.target_root / "profiles",
            self.target_root / "state",
            self.target_root / "migration",
            self.target_root / "migration/openclaw",
            self.target_root / "migration/openclaw/restore-points",
            self.target_root / "migration/openclaw/restore-points" / snapshot_hex,
        ]
        for profile in profiles:
            root = self.target_root / profile["target_profile_root"]
            controlled_ancestors.extend(
                [
                    root,
                    root / "workspace",
                    root / ".hermes-openclaw-profile.json",
                    root / "workspace/.hermes-openclaw-workspace.json",
                ]
            )
        symlink_targets: set[str] = set()
        for path in controlled_ancestors:
            if self._has_symlink_component(path):
                try:
                    target = path.relative_to(self.target_root).as_posix() or "."
                except ValueError:
                    target = "."
                symlink_targets.add(target)
        conflicts.extend(
            {"code": "target_ancestor_symlink", "target": target}
            for target in sorted(symlink_targets)
        )

        for profile in profiles:
            root = self.target_root / profile["target_profile_root"]
            if not os.path.lexists(root):
                continue
            if not self._owned_profile_is_valid(profile, source_snapshot):
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
        if self._has_symlink_component(manifest_path):
            conflicts.append(
                {
                    "code": "target_manifest_invalid",
                    "target": "migration/openclaw/multi-agent-manifest.json",
                }
            )
        restore_point = (
            self.target_root / "migration/openclaw/restore-points" / snapshot_hex
        )
        if os.path.lexists(restore_point) and not self._restore_point_is_valid(
            restore_point, profiles, source_snapshot
        ):
            conflicts.append(
                {
                    "code": "restore_point_invalid",
                    "target": restore_point.relative_to(self.target_root).as_posix(),
                }
            )
        return sorted(
            conflicts,
            key=lambda item: (item["code"], item["target"], item.get("record_id", "")),
        )

    def _validate_manifest(self, manifest: Any) -> None:
        expected_top_level = {
            "binding_records",
            "classified_source_refs",
            "conflicts",
            "counts",
            "planned_target_refs",
            "planned_writes",
            "profile_records",
            "review_items",
            "schema_version",
            "source_snapshot",
        }
        if (
            not isinstance(manifest, dict)
            or set(manifest) != expected_top_level
            or manifest.get("schema_version") != MANIFEST_SCHEMA
        ):
            raise MultiAgentMigrationError("manifest_invalid")
        profiles = manifest.get("profile_records")
        bindings = manifest.get("binding_records")
        source_snapshot = manifest.get("source_snapshot")
        if (
            not isinstance(profiles, list)
            or not isinstance(bindings, list)
            or not isinstance(source_snapshot, dict)
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(source_snapshot.get("id", ""))
            )
        ):
            raise MultiAgentMigrationError("manifest_invalid")
        profile_ids: set[str] = set()
        for profile in profiles:
            expected_keys = {
                "agent_id",
                "profile_id",
                "profile_kind",
                "source_workspace_ref",
                "target_profile_root",
                "target_workspace_root",
            }
            if not isinstance(profile, dict) or set(profile) != expected_keys:
                raise MultiAgentMigrationError("manifest_invalid")
            profile_id = profile.get("profile_id")
            if (
                not isinstance(profile_id, str)
                or not _PROFILE_RE.fullmatch(profile_id)
                or profile_id in profile_ids
                or profile.get("profile_kind") not in {"dm", "group", "functional"}
            ):
                raise MultiAgentMigrationError("manifest_invalid")
            profile_ids.add(profile_id)
            profile_root = self._safe_relative_target(profile["target_profile_root"])
            workspace_root = self._safe_relative_target(
                profile["target_workspace_root"]
            )
            if (
                profile_root != PurePosixPath("profiles") / profile_id
                or workspace_root != profile_root / "workspace"
            ):
                raise MultiAgentMigrationError("manifest_invalid")
            source_ref = PurePosixPath(str(profile.get("source_workspace_ref", "")))
            if source_ref.is_absolute() or ".." in source_ref.parts or not source_ref.parts:
                raise MultiAgentMigrationError("manifest_invalid")
        record_ids: set[str] = set()
        for record in bindings:
            if not isinstance(record, dict) or not isinstance(record.get("action"), str):
                raise MultiAgentMigrationError("manifest_invalid")
            record_id = record.get("record_id")
            if (
                not isinstance(record_id, str)
                or record_id in record_ids
                or record.get("profile") not in profile_ids
            ):
                raise MultiAgentMigrationError("manifest_invalid")
            record_ids.add(record_id)
            action = record["action"]
            if action == "materialize":
                expected = {
                    "action",
                    "identity_digest",
                    "kind",
                    "platform",
                    "profile",
                    "record_id",
                    "registry_key",
                }
                if (
                    set(record) != expected
                    or record.get("kind") not in {"dm", "group"}
                    or record.get("platform") != "feishu"
                    or not re.fullmatch(
                        r"sha256:[0-9a-f]{64}", str(record.get("identity_digest", ""))
                    )
                    or record.get("registry_key")
                    != f"feishu:{record.get('kind')}:{record.get('identity_digest')}"
                ):
                    raise MultiAgentMigrationError("manifest_invalid")
            elif action == "review":
                if set(record) != {
                    "action",
                    "profile",
                    "reason",
                    "record_id",
                    "source_binding_sha256",
                }:
                    raise MultiAgentMigrationError("manifest_invalid")
            elif action == "resolved":
                if (
                    set(record)
                    != {
                        "action",
                        "outcome",
                        "profile",
                        "reason_code",
                        "record_id",
                        "source_binding_sha256",
                    }
                    or record.get("outcome") != "retire"
                    or record.get("reason_code") != RETIRE_REASON
                ):
                    raise MultiAgentMigrationError("manifest_invalid")
            else:
                raise MultiAgentMigrationError("manifest_invalid")
            if action in {"review", "resolved"} and not re.fullmatch(
                r"[0-9a-f]{64}", str(record.get("source_binding_sha256", ""))
            ):
                raise MultiAgentMigrationError("manifest_invalid")
        expected_counts = {
            "bindings": len(bindings),
            "functional_profiles": sum(
                profile["profile_kind"] == "functional" for profile in profiles
            ),
            "group_profiles": sum(
                profile["profile_kind"] == "group" for profile in profiles
            ),
            "materialized_bindings": sum(
                record["action"] == "materialize" for record in bindings
            ),
            "profiles": len(profiles),
            "resolved_bindings": sum(
                record["action"] == "resolved" for record in bindings
            ),
            "review_bindings": sum(
                record["action"] == "review" for record in bindings
            ),
            "user_profiles": sum(
                profile["profile_kind"] == "dm" for profile in profiles
            ),
        }
        if manifest.get("counts") != expected_counts:
            raise MultiAgentMigrationError("manifest_invalid")
        planned_target_refs = manifest.get("planned_target_refs")
        planned_writes = manifest.get("planned_writes")
        classified_refs = manifest.get("classified_source_refs")
        if not all(
            isinstance(value, list)
            for value in (planned_target_refs, planned_writes, classified_refs)
        ):
            raise MultiAgentMigrationError("manifest_invalid")
        for target in planned_target_refs:
            self._safe_relative_target(target)
        for write in planned_writes:
            if not isinstance(write, dict) or set(write) != {"action", "target"}:
                raise MultiAgentMigrationError("manifest_invalid")
            self._safe_relative_target(write["target"])
        for source_ref in classified_refs:
            if not isinstance(source_ref, dict):
                raise MultiAgentMigrationError("manifest_invalid")
            path = PurePosixPath(str(source_ref.get("relative_path", "")))
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise MultiAgentMigrationError("manifest_invalid")

    def apply(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """Apply one reviewed manifest, rolling back this call on failure."""
        self._validate_manifest(manifest)
        current = self.preview()
        if current["source_snapshot"]["id"] != manifest.get("source_snapshot", {}).get(
            "id"
        ):
            raise MultiAgentMigrationError("source_drift")
        if manifest.get("conflicts") or current["conflicts"]:
            raise MultiAgentMigrationError("target_conflict")
        if canonical_manifest_bytes(manifest) != canonical_manifest_bytes(current):
            raise MultiAgentMigrationError("manifest_mismatch")
        if current["review_items"] or current["counts"]["review_bindings"]:
            raise MultiAgentMigrationError("binding_unresolved")

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
            "binding_outcomes": {
                "materialized": manifest["counts"]["materialized_bindings"],
                "resolved": manifest["counts"]["resolved_bindings"],
            },
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
        source_snapshot = manifest["source_snapshot"]["id"]
        for profile in manifest["profile_records"]:
            root = self.target_root / profile["target_profile_root"]
            if not os.path.lexists(root):
                continue
            if not self._owned_profile_is_valid(profile, source_snapshot):
                raise MultiAgentMigrationError("target_profile_conflict")
        snapshot_hex = source_snapshot.removeprefix("sha256:")
        restore_point = (
            self.target_root / "migration/openclaw/restore-points" / snapshot_hex
        )
        if os.path.lexists(restore_point) and not self._restore_point_is_valid(
            restore_point, manifest["profile_records"], source_snapshot
        ):
            raise MultiAgentMigrationError("restore_point_invalid")

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
                **self._workspace_marker(
                    profile, manifest["source_snapshot"]["id"]
                )
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
                "backup_sha256": {
                    "manifest": (
                        _sha256_bytes(manifest_before)
                        if manifest_before is not None
                        else None
                    ),
                    "registry": (
                        _sha256_bytes(registry_before)
                        if registry_before is not None
                        else None
                    ),
                },
                "created_profile_roots": [
                    profile["target_profile_root"]
                    for profile in manifest["profile_records"]
                    if not (self.target_root / profile["target_profile_root"]).exists()
                ],
                "had_manifest": manifest_before is not None,
                "had_registry": registry_before is not None,
                "manifest_target": "migration/openclaw/multi-agent-manifest.json",
                "registry_target": "state/profile-identity-registry.json",
                "schema_version": RESTORE_POINT_SCHEMA,
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
