#!/usr/bin/env python3
"""Build a private Hermes multi-profile home from a live OpenClaw data root.

The tool is deliberately host-local: credentials are copied directly between
private files and never emitted in stdout or in the migration manifest. Chat
sessions, trajectory files, indexes, caches, and external-memory configuration
are never inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA = "tode68-openclaw-hermes-migration/v1"
REGISTRY_SCHEMA = "hermes-profile-identity-registry/v1"
MEMORY_LIMIT = 4_000
USER_LIMIT = 1_200
MAX_AUTO_DOCUMENT = 100 * 1024 * 1024

DENIED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "auth",
    "backup",
    "backups",
    "cache",
    "checkpoints",
    "deleted",
    "dist",
    "logs",
    "media",
    "node_modules",
    "resets",
    "sessions",
    "site-packages",
    "trajectory",
    "trajectories",
    "venv",
}
DENIED_SUFFIXES = {
    ".db",
    ".db-shm",
    ".db-wal",
    ".idx",
    ".index",
    ".log",
    ".pyc",
    ".so",
    ".sqlite",
    ".sqlite3",
    ".trajectory.jsonl",
}
DOCUMENT_SUFFIXES = {
    ".bas",
    ".bat",
    ".cfg",
    ".csv",
    ".css",
    ".dat",
    ".doc",
    ".docx",
    ".dotm",
    ".dxt",
    ".geojson",
    ".html",
    ".jpeg",
    ".jira",
    ".jpg",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".network",
    ".pdf",
    ".pfo",
    ".png",
    ".pptx",
    ".ps1",
    ".py",
    ".rels",
    ".rst",
    ".service",
    ".sh",
    ".swi",
    ".svg",
    ".tc",
    ".timer",
    ".toml",
    ".tr",
    ".ts",
    ".txt",
    ".vml",
    ".vsdx",
    ".xls",
    ".xlsx",
    ".xml",
    ".xsd",
    ".yaml",
    ".yml",
}
SECRET_NAME = re.compile(
    r"(?i)(?:^|[._-])(auth|credential|password|secret|token)(?:[._-]|$)|^\.env"
)
SECRET_CONTENT = re.compile(
    rb"(?i)(?:api[_-]?key|access[_-]?token|bot[_-]?token|client[_-]?secret|"
    rb"password|authorization)[\"']?\s*[:=]\s*[\"']?(?:bearer\s+)?|"
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    rb"\b(?:sk|xox[baprs]|gh[pousr])-[A-Za-z0-9_-]{8,}"
)
TRANSIENT_MEMORY_CONTENT = re.compile(
    rb"(?i)(?:"
    rb"promoted from short-term memory|conversation summary|"
    rb"(?:deep|rem|light) sleep|session key|session id|"
    rb"openclaw-memory-promotion|untrusted metadata|"
    rb"\[score=[0-9.] +recalls=|promoted \d+ candidate"
    rb")"
)


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProfileRecord:
    profile: str
    kind: str
    source_agent: str
    source_workspace: Path
    identity_digest: str | None = None
    home_chat_id: str | None = None


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def atomic_write(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise MigrationError(f"required regular JSON missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MigrationError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise MigrationError(f"JSON root is not an object: {path}")
    return value


def safe_workspace(source: Path, raw: Any) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise MigrationError("agent workspace is missing")
    path = Path(raw)
    if not path.is_absolute():
        path = source / path
    unresolved = Path(os.path.abspath(path))
    try:
        relative = unresolved.relative_to(source)
    except ValueError as exc:
        raise MigrationError(f"workspace outside source: {raw}") from exc
    probe = source
    for part in relative.parts:
        probe /= part
        if probe.is_symlink():
            raise MigrationError(f"workspace symlink rejected: {raw}")
    if not unresolved.is_dir():
        raise MigrationError(f"workspace missing: {raw}")
    return unresolved


def registry_path(source: Path, kind: str) -> Path:
    candidates = {
        "dm": [
            source / "automation/users.json",
            source / "automation/auto-user-isolation/users.json",
        ],
        "group": [
            source / "automation/groups.json",
            source / "automation/auto-group-isolation/groups.json",
        ],
    }[kind]
    found = [path for path in candidates if path.is_file() and not path.is_symlink()]
    if len(found) != 1:
        raise MigrationError(f"expected one {kind} registry, found {len(found)}")
    return found[0]


def load_registry(source: Path, kind: str) -> list[tuple[str, str]]:
    data = read_json(registry_path(source, kind))
    entries: list[tuple[str, str]] = []
    if isinstance(data.get("entries"), list):
        for row in data["entries"]:
            if not isinstance(row, dict):
                raise MigrationError(f"invalid {kind} registry entry")
            identity, agent = row.get("identity"), row.get("agent_id")
            if not isinstance(identity, str) or not isinstance(agent, str):
                raise MigrationError(f"invalid {kind} registry identity")
            entries.append((identity, agent))
    else:
        for key, agent in data.items():
            if not isinstance(key, str) or not isinstance(agent, str):
                raise MigrationError(f"invalid {kind} registry mapping")
            identity = key.removeprefix("feishu:")
            entries.append((identity, agent))
    identities = [identity for identity, _agent in entries]
    if len(identities) != len(set(identities)):
        raise MigrationError(f"duplicate {kind} identity")
    return sorted(entries)


def identity_digest(kind: str, identity: str) -> str:
    return hashlib.sha256(f"feishu\0{kind}\0{identity}".encode()).hexdigest()


def profile_name(kind: str, digest: str) -> str:
    return f"feishu-{kind}-{digest[:16]}"


def inventory(source: Path) -> tuple[list[ProfileRecord], dict[str, Any]]:
    config = read_json(source / "openclaw.json")
    agents_value = (config.get("agents") or {}).get("list")
    bindings = config.get("bindings")
    if not isinstance(agents_value, list) or not isinstance(bindings, list):
        raise MigrationError("openclaw agents.list/bindings are invalid")
    agents: dict[str, Path] = {}
    for row in agents_value:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise MigrationError("invalid agent record")
        agent = row["id"].strip()
        if agent in agents:
            raise MigrationError(f"duplicate agent: {agent}")
        agents[agent] = safe_workspace(source, row.get("workspace"))

    profiles: list[ProfileRecord] = []
    registry_bindings: dict[str, dict[str, str]] = {}
    referenced_agents: set[str] = set()
    for kind in ("dm", "group"):
        for identity, agent in load_registry(source, kind):
            if agent not in agents:
                raise MigrationError(f"registry references unknown agent: {agent}")
            digest = identity_digest(kind, identity)
            profile = profile_name(kind, digest)
            profiles.append(
                ProfileRecord(profile, kind, agent, agents[agent], digest, identity)
            )
            registry_bindings[f"feishu:{kind}:sha256:{digest}"] = {
                "identity_digest": f"sha256:{digest}",
                "kind": kind,
                "platform": "feishu",
                "profile": profile,
            }
            referenced_agents.add(agent)

    for agent, workspace in sorted(agents.items()):
        if agent not in referenced_agents:
            profiles.append(ProfileRecord(agent.lower(), "unbound", agent, workspace))

    if len({profile.profile for profile in profiles}) != len(profiles):
        raise MigrationError("target profile collision")

    peer_bindings: list[tuple[str, str, str]] = []
    malformed = 0
    for row in bindings:
        if not isinstance(row, dict) or not isinstance(row.get("match"), dict):
            malformed += 1
            continue
        peer = row["match"].get("peer")
        if not isinstance(peer, dict):
            malformed += 1
            continue
        raw_kind = peer.get("kind")
        kind = "dm" if raw_kind in {"direct", "dm", "user"} else "group"
        identity = peer.get("id")
        agent = row.get("agentId")
        if not isinstance(identity, str) or not isinstance(agent, str):
            malformed += 1
            continue
        peer_bindings.append((kind, identity, agent))
    unique_binding_identities = {(kind, identity) for kind, identity, _ in peer_bindings}
    duplicate_binding_rows = len(peer_bindings) - len(unique_binding_identities)
    registry_pairs = {
        (kind, identity, agent)
        for kind in ("dm", "group")
        for identity, agent in load_registry(source, kind)
    }
    mismatched_binding_rows = sum(row not in registry_pairs for row in peer_bindings)

    facts = {
        "agent_count": len(agents),
        "binding_count": len(bindings),
        "duplicate_binding_rows": duplicate_binding_rows,
        "identity_profile_count": len(registry_bindings),
        "malformed_binding_rows": malformed,
        "mismatched_binding_rows": mismatched_binding_rows,
        "profile_count": len(profiles),
        "registry": {
            "bindings": registry_bindings,
            "schema_version": REGISTRY_SCHEMA,
        },
        "unbound_profile_count": sum(profile.kind == "unbound" for profile in profiles),
    }
    return sorted(profiles, key=lambda item: item.profile), facts


def memory_sources(workspace: Path) -> list[Path]:
    result: set[Path] = set()
    for relative in ("MEMORY.md", "USER.md"):
        path = workspace / relative
        if path.is_file() and not path.is_symlink():
            result.add(path)
    for dirname in ("memory", "memories"):
        root = workspace / dirname
        if root.is_dir() and not root.is_symlink():
            for path in root.rglob("*.md"):
                if path.is_file() and not path.is_symlink():
                    result.add(path)
    return sorted(result)


def text_chunks(payload: str) -> list[str]:
    parts = re.split(r"\n\s*§\s*\n|\n{2,}", payload)
    return [part.strip() for part in parts if part.strip()]


def safe_memory_chunk(chunk: str) -> bool:
    raw = chunk.encode("utf-8", errors="ignore")
    return (
        len(raw) <= 8_000
        and not SECRET_CONTENT.search(raw)
        and not TRANSIENT_MEMORY_CONTENT.search(raw)
    )


def compact_memory(
    workspace: Path,
    paths: list[Path],
    *,
    target_name: str,
    limit: int,
) -> tuple[str, list[dict[str, str]]]:
    explicit = workspace / target_name
    ordered = []
    if explicit in paths:
        ordered.append(explicit)
    if target_name == "MEMORY.md":
        ordered.extend(
            sorted(
                (path for path in paths if path != explicit and path.name != "USER.md"),
                key=lambda path: (path.stat().st_mtime_ns, path.as_posix()),
                reverse=True,
            )
        )
    provenance: list[dict[str, str]] = []
    chunks: list[str] = []
    seen: set[str] = set()
    used = 0
    for path in ordered:
        try:
            payload = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        source_hash = sha256_file(path)
        for chunk in text_chunks(payload):
            normalized = " ".join(chunk.split()).casefold()
            digest = sha256_bytes(normalized.encode())
            if digest in seen or not safe_memory_chunk(chunk):
                continue
            separator = 3 if chunks else 0
            remaining = limit - used - separator
            if remaining <= 0:
                break
            selected = chunk if len(chunk) <= remaining else chunk[:remaining].rstrip()
            if not selected:
                break
            chunks.append(selected)
            used += separator + len(selected)
            seen.add(digest)
            provenance.append(
                {
                    "entry_sha256": sha256_bytes(selected.encode()),
                    "source_path": path.relative_to(workspace).as_posix(),
                    "source_sha256": source_hash,
                }
            )
            if used >= limit:
                break
        if used >= limit:
            break
    return "\n§\n".join(chunks), provenance


def archive_memory(
    workspace: Path, paths: list[Path], target: Path
) -> dict[str, Any]:
    objects = target / "objects"
    objects.mkdir(parents=True, exist_ok=True)
    by_hash: dict[str, dict[str, Any]] = {}
    for path in paths:
        digest = sha256_file(path)
        row = by_hash.setdefault(digest, {"paths": [], "sha256": digest})
        row["paths"].append(path.relative_to(workspace).as_posix())
        object_path = objects / f"{digest}.md"
        if not object_path.exists():
            shutil.copy2(path, object_path, follow_symlinks=False)
    rows = sorted(by_hash.values(), key=lambda row: row["sha256"])
    atomic_write(target / "index.json", canonical_json({"objects": rows}))
    for path in target.rglob("*"):
        if path.is_file():
            path.chmod(0o440)
        elif path.is_dir():
            path.chmod(0o550)
    target.chmod(0o550)
    return {"objects": len(rows), "source_paths": len(paths)}


def copy_skill_tree(source: Path, target: Path) -> None:
    if target.exists():
        raise MigrationError(f"skill target collision: {target.name}")
    if source.is_symlink() or any(path.is_symlink() for path in source.rglob("*")):
        raise MigrationError(f"skill symlink rejected: {source.name}")
    shutil.copytree(source, target, symlinks=False)
    for path in target.rglob("*"):
        if path.is_file():
            path.chmod(0o440)
        elif path.is_dir():
            path.chmod(0o550)
    target.chmod(0o550)


def copy_review_skill_tree(source: Path, target: Path) -> None:
    """Archive only regular, non-secret files from a non-activatable Skill."""
    if target.exists():
        raise MigrationError(f"skill review target collision: {target.name}")
    target.mkdir(parents=True)
    for directory, dirnames, filenames in os.walk(source, topdown=True, followlinks=False):
        current = Path(directory)
        relative_dir = current.relative_to(source)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not (current / name).is_symlink()
            and name.lower() not in DENIED_PARTS
        )
        destination_dir = target / relative_dir
        destination_dir.mkdir(parents=True, exist_ok=True)
        for name in sorted(filenames):
            path = current / name
            if path.is_symlink() or not path.is_file() or SECRET_NAME.search(name):
                continue
            with path.open("rb") as handle:
                sample = handle.read(1024 * 1024)
            if SECRET_CONTENT.search(sample):
                continue
            shutil.copy2(path, destination_dir / name, follow_symlinks=False)
    for path in target.rglob("*"):
        if path.is_file():
            path.chmod(0o440)
        elif path.is_dir():
            path.chmod(0o550)
    target.chmod(0o550)


def classify_skills(source: Path, build: Path) -> dict[str, Any]:
    roots = [source / "skills", source / "workspace/skills"]
    canonical = build / "shared-skills"
    review = build / "migration/review-skills"
    canonical.mkdir(parents=True)
    review.mkdir(parents=True)
    records: list[dict[str, str]] = []
    names: dict[str, str] = {}
    for root in roots:
        if not root.is_dir() or root.is_symlink():
            continue
        for skill_file in sorted(root.glob("*/SKILL.md")):
            skill_dir = skill_file.parent
            payload = skill_file.read_bytes()
            digest = sha256_bytes(payload)
            name = skill_dir.name
            if names.get(name) == digest:
                records.append({"action": "deduplicate", "name": name, "sha256": digest})
                continue
            if name in names:
                name = f"{name}-{digest[:8]}"
            names[name] = digest
            has_symlink = skill_dir.is_symlink() or any(
                path.is_symlink() for path in skill_dir.rglob("*")
            )
            has_secret = False
            for path in skill_dir.rglob("*"):
                if path.is_symlink() or not path.is_file():
                    continue
                if SECRET_NAME.search(path.name):
                    has_secret = True
                    break
                with path.open("rb") as handle:
                    if SECRET_CONTENT.search(handle.read(1024 * 1024)):
                        has_secret = True
                        break
            openclaw_only = bool(
                re.search(rb"(?i)\bopenclaw\b|\bopenclaw\.json\b", payload)
            )
            incompatible = has_symlink or has_secret or openclaw_only
            destination = (review if incompatible else canonical) / name
            if incompatible:
                copy_review_skill_tree(skill_dir, destination)
            else:
                copy_skill_tree(skill_dir, destination)
            reasons = [
                reason
                for condition, reason in (
                    (has_symlink, "symlink"),
                    (has_secret, "secret-content"),
                    (openclaw_only, "openclaw-specific"),
                )
                if condition
            ]
            records.append(
                {
                    "action": "review" if incompatible else "activate",
                    "name": name,
                    "reason": ",".join(reasons) if reasons else "compatible",
                    "sha256": digest,
                }
            )
    return {
        "activated": sum(row["action"] == "activate" for row in records),
        "records": records,
        "review": sum(row["action"] == "review" for row in records),
    }


def install_shared_skills(shared: Path, profile_root: Path) -> int:
    target_root = profile_root / "skills"
    count = 0
    for skill in sorted(shared.iterdir()):
        if not skill.is_dir():
            continue
        copy_skill_tree(skill, target_root / skill.name)
        count += 1
    search_target = target_root / "legacy-memory-search"
    search_target.mkdir(parents=True)
    atomic_write(
        search_target / "SKILL.md",
        (
            "# 旧 Memory 本地检索\n\n"
            "仅在当前 Profile 的内置 MEMORY.md 信息不足时使用。执行 "
            "`python scripts/search.py <关键词>` 检索本 Profile 私有、只读的 "
            "legacy Memory archive。不得传入绝对路径或路径穿越参数，不得访问其他 Profile。\n"
        ).encode(),
        0o440,
    )
    atomic_write(search_target / "scripts/search.py", SEARCH_SCRIPT.encode(), 0o550)
    return count + 1


SEARCH_SCRIPT = r'''#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("query")
parser.add_argument("--limit", type=int, default=20)
args = parser.parse_args()
if not args.query.strip() or args.limit < 1 or args.limit > 100:
    raise SystemExit("invalid query or limit")
profile = Path(__file__).resolve().parents[2]
archive = profile / "legacy-memory"
probe = profile
for part in ("legacy-memory", "objects"):
    probe = probe / part
    if probe.is_symlink():
        raise SystemExit("archive symlink rejected")
root = (archive / "objects").resolve(strict=True)
if not root.is_relative_to(profile.resolve(strict=True)):
    raise SystemExit("archive escaped profile")
query = args.query.casefold()
hits = []
for path in sorted(root.glob("*.md")):
    if path.is_symlink() or not path.is_file():
        continue
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise SystemExit("archive object escaped root")
    text = path.read_text(encoding="utf-8", errors="replace")
    pos = text.casefold().find(query)
    if pos >= 0:
        hits.append({"object": path.name, "excerpt": text[max(0,pos-160):pos+360]})
        if len(hits) >= args.limit:
            break
print(json.dumps({"hits": hits}, ensure_ascii=False, indent=2))
'''


def document_action(path: Path, workspace: Path) -> tuple[str, str]:
    relative = path.relative_to(workspace)
    lowered = {part.lower() for part in relative.parts}
    if lowered & DENIED_PARTS or any(path.name.lower().endswith(s) for s in DENIED_SUFFIXES):
        return "exclude", "denied-path"
    if relative.parts and relative.parts[0].lower() in {"memory", "memories", "skills"}:
        return "exclude", "handled-separately"
    if path.name in {"MEMORY.md", "USER.md"}:
        return "exclude", "handled-separately"
    if SECRET_NAME.search(path.name):
        return "exclude", "secret-name"
    if path.suffix.lower() not in DOCUMENT_SUFFIXES:
        return "review", "unknown-type"
    size = path.stat(follow_symlinks=False).st_size
    if size > MAX_AUTO_DOCUMENT:
        return "review", "oversized"
    with path.open("rb") as handle:
        sample = handle.read(1024 * 1024)
    if SECRET_CONTENT.search(sample):
        return "exclude", "secret-content"
    return "copy", "durable-document"


def copy_documents(workspace: Path, target: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    copied = 0
    copied_bytes = 0
    for directory, dirnames, filenames in os.walk(workspace, topdown=True, followlinks=False):
        current = Path(directory)
        kept_dirs: list[str] = []
        for name in sorted(dirnames):
            path = current / name
            relative = path.relative_to(workspace)
            if path.is_symlink():
                records.append(
                    {"action": "review", "path": relative.as_posix(), "reason": "symlink"}
                )
            elif name.lower() in DENIED_PARTS:
                records.append(
                    {"action": "exclude", "path": relative.as_posix(), "reason": "denied-directory"}
                )
            elif relative.parts[0].lower() in {"memory", "memories", "skills"}:
                records.append(
                    {"action": "exclude", "path": relative.as_posix(), "reason": "handled-separately"}
                )
            else:
                kept_dirs.append(name)
        dirnames[:] = kept_dirs
        for name in sorted(filenames):
            path = current / name
            relative = path.relative_to(workspace)
            if path.is_symlink():
                records.append(
                    {"action": "review", "path": relative.as_posix(), "reason": "symlink"}
                )
                continue
            if not path.is_file():
                records.append(
                    {"action": "review", "path": relative.as_posix(), "reason": "special-file"}
                )
                continue
            action, reason = document_action(path, workspace)
            row: dict[str, Any] = {
                "action": action,
                "path": relative.as_posix(),
                "reason": reason,
                "size": path.stat(follow_symlinks=False).st_size,
            }
            if action in {"copy", "review"}:
                row["sha256"] = sha256_file(path)
            records.append(row)
            if action == "copy":
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination, follow_symlinks=False)
                copied += 1
                copied_bytes += row["size"]
    return {
        "copied": copied,
        "copied_bytes": copied_bytes,
        "excluded": sum(row["action"] == "exclude" for row in records),
        "records": records,
        "review": sum(row["action"] == "review" for row in records),
    }


def yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def profile_config(profile: str, *, home_chat_id: str | None = None) -> str:
    home_channel = ""
    if home_chat_id:
        home_channel = f"""platforms:
  feishu:
    home_channel:
      platform: feishu
      chat_id: {yaml_string(home_chat_id)}
      name: {yaml_string(profile)}
"""
    return f"""_config_version: 12
timezone: Asia/Shanghai
model:
  default: deepseek-v4-flash
  provider: tode
providers:
  tode:
    name: TODE NewAPI
    base_url: https://newapi.tode.ltd/v1
    key_env: OPENAI_API_KEY
    api_mode: chat_completions
    default_model: deepseek-v4-flash
    models:
      deepseek-v4-flash:
        context_length: 1000000
      deepseek-v4-pro:
        context_length: 1000000
      glm-5-turbo:
        context_length: 202752
fallback_providers: []
mcp_servers:
  zhipu-web-search:
    url: https://open.bigmodel.cn/api/mcp/web_search_prime/mcp
    headers:
      Authorization: "Bearer ${{ZHIPU_API_KEY}}"
  zhipu-web-reader:
    url: https://open.bigmodel.cn/api/mcp/web_reader/mcp
    headers:
      Authorization: "Bearer ${{ZHIPU_API_KEY}}"
  zhipu-zread:
    url: https://open.bigmodel.cn/api/mcp/zread/mcp
    headers:
      Authorization: "Bearer ${{ZHIPU_API_KEY}}"
agent:
  max_turns: 1000
  gateway_timeout: 3600
  reasoning_effort: high
approvals:
  destructive_slash_confirm: false
display:
  show_commentary: false
  memory_notifications: "off"
  background_process_notifications: "off"
  tool_progress_command: false
  platforms:
    feishu:
      tool_progress: "off"
      show_reasoning: false
      thinking_progress: false
      streaming: false
      interim_assistant_messages: false
      long_running_notifications: false
      busy_ack_detail: false
      busy_steer_ack_enabled: false
      live_status: "off"
{home_channel}
terminal:
  backend: local
  cwd: {yaml_string(f'/opt/data/profiles/{profile}/workspace')}
  timeout: 300
memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: {MEMORY_LIMIT}
  user_char_limit: {USER_LIMIT}
  write_approval: false
"""


def root_config() -> str:
    return profile_config("default").replace(
        "/opt/data/profiles/default/workspace", "/opt/data/workspace"
    ) + """gateway:
  multiplex_profiles: true
  group_sessions_per_user: false
  thread_sessions_per_user: false
  max_concurrent_sessions: 8
  platforms:
    feishu:
      enabled: true
      typing_indicator: true
      gateway_restart_notification: false
      extra:
        profile_auto_provision: true
        require_mention: true
        connection_mode: websocket
        domain: feishu
"""


def extract_secrets(source: Path) -> tuple[dict[str, str], dict[str, str]]:
    config = read_json(source / "openclaw.json")
    providers = ((config.get("models") or {}).get("providers") or {})
    tode = providers.get("tode") or {}
    provider_key = tode.get("apiKey") or tode.get("api_key")
    feishu = (config.get("channels") or {}).get("feishu") or {}
    primary = {
        "OPENAI_API_KEY": provider_key,
        "FEISHU_APP_ID": feishu.get("appId"),
        "FEISHU_APP_SECRET": feishu.get("appSecret"),
        "FEISHU_DOMAIN": str(feishu.get("domain") or "feishu"),
        "FEISHU_CONNECTION_MODE": "websocket",
        "FEISHU_ALLOW_ALL_USERS": "true",
        "FEISHU_GROUP_POLICY": "open",
        "FEISHU_REQUIRE_MENTION": "true",
    }
    secondary_source = ((feishu.get("accounts") or {}).get("dingjiayu") or {})
    secondary = dict(primary)
    secondary["FEISHU_APP_ID"] = secondary_source.get("appId")
    secondary["FEISHU_APP_SECRET"] = secondary_source.get("appSecret")
    for name, values in (("primary", primary), ("secondary", secondary)):
        missing = [key for key in ("OPENAI_API_KEY", "FEISHU_APP_ID", "FEISHU_APP_SECRET") if not values.get(key)]
        if missing:
            raise MigrationError(f"{name} secret source missing required keys: {','.join(missing)}")
    return primary, secondary


def env_bytes(values: dict[str, str], *, provider_only: bool = False) -> bytes:
    keys = ["OPENAI_API_KEY"] if provider_only else sorted(values)
    lines = []
    for key in keys:
        value = str(values[key]).replace("\r", "").replace("\n", "")
        lines.append(f"{key}={value}")
    return ("\n".join(lines) + "\n").encode()


def build_profile(
    record: ProfileRecord,
    root: Path,
    shared_skills: Path,
    provider_secrets: dict[str, str],
) -> dict[str, Any]:
    profile = root / "profiles" / record.profile
    for dirname in ("workspace", "sessions", "skills", "memories", "logs", "plans", "cron", "home"):
        (profile / dirname).mkdir(parents=True, exist_ok=True)
    memory_paths = memory_sources(record.source_workspace)
    memory_text, memory_provenance = compact_memory(
        record.source_workspace, memory_paths, target_name="MEMORY.md", limit=MEMORY_LIMIT
    )
    user_text, user_provenance = compact_memory(
        record.source_workspace, memory_paths, target_name="USER.md", limit=USER_LIMIT
    )
    memory_payload = memory_text + ("\n" if memory_text and len(memory_text) < MEMORY_LIMIT else "")
    user_payload = user_text + ("\n" if user_text and len(user_text) < USER_LIMIT else "")
    atomic_write(profile / "memories/MEMORY.md", memory_payload.encode())
    atomic_write(profile / "memories/USER.md", user_payload.encode())
    archive = archive_memory(record.source_workspace, memory_paths, profile / "legacy-memory")
    soul_parts = []
    for name in ("SOUL.md", "IDENTITY.md"):
        path = record.source_workspace / name
        if path.is_file() and not path.is_symlink():
            soul_parts.append(path.read_text(encoding="utf-8", errors="replace"))
    if not soul_parts:
        soul_parts.append("你是该飞书聊天的独立 Hermes Agent。只使用当前 Profile 的资料和 Memory。")
    atomic_write(profile / "SOUL.md", ("\n\n".join(soul_parts).strip() + "\n").encode())
    atomic_write(
        profile / "config.yaml",
        profile_config(record.profile, home_chat_id=record.home_chat_id).encode(),
    )
    atomic_write(profile / ".env", env_bytes(provider_secrets, provider_only=True))
    skills = install_shared_skills(shared_skills, profile)
    documents = copy_documents(record.source_workspace, profile / "workspace")
    return {
        "archive": archive,
        "documents": {key: value for key, value in documents.items() if key != "records"},
        "document_records": documents["records"],
        "identity_digest": f"sha256:{record.identity_digest}" if record.identity_digest else None,
        "kind": record.kind,
        "memory_chars": len(memory_text),
        "memory_provenance": memory_provenance,
        "profile": record.profile,
        "skills": skills,
        "source_agent": record.source_agent,
        "source_workspace": record.source_workspace.name,
        "user_chars": len(user_text),
        "user_provenance": user_provenance,
    }


def validate_result(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    profiles = manifest["profiles"]
    for row in profiles:
        profile = root / "profiles" / row["profile"]
        required = [
            profile / "SOUL.md",
            profile / ".env",
            profile / "config.yaml",
            profile / "memories/MEMORY.md",
            profile / "memories/USER.md",
            profile / "legacy-memory/index.json",
            profile / "workspace",
        ]
        if any(not path.exists() for path in required):
            errors.append(f"profile_incomplete:{row['profile']}")
        if len((profile / "memories/MEMORY.md").read_text()) > MEMORY_LIMIT:
            errors.append(f"memory_overflow:{row['profile']}")
        if len((profile / "memories/USER.md").read_text()) > USER_LIMIT:
            errors.append(f"user_overflow:{row['profile']}")
        env_text = (profile / ".env").read_text()
        if "FEISHU_" in env_text:
            errors.append(f"secondary_transport_secret:{row['profile']}")
        config_text = (profile / "config.yaml").read_text().casefold()
        if re.search(r"(?m)^\s+provider:\s*(hindsight|honcho|mem0|openviking)", config_text):
            errors.append(f"external_memory_provider:{row['profile']}")
    registry = read_json(root / "state/profile-identity-registry.json")
    if len(registry.get("bindings", {})) != manifest["inventory"]["identity_profile_count"]:
        errors.append("registry_count_mismatch")
    return {"errors": errors, "passed": not errors, "profiles": len(profiles)}


def run(source: Path, target: Path) -> dict[str, Any]:
    source = source.resolve(strict=True)
    if target.exists() or target.is_symlink():
        raise MigrationError(f"target already exists: {target}")
    profiles, facts = inventory(source)
    primary_secrets, secondary_secrets = extract_secrets(source)
    build = target.with_name(f".{target.name}.build-{os.getpid()}")
    if build.exists():
        raise MigrationError(f"staging collision: {build}")
    build.mkdir(parents=True, mode=0o700)
    try:
        skill_manifest = classify_skills(source, build)
        profile_rows = [
            build_profile(record, build, build / "shared-skills", primary_secrets)
            for record in profiles
        ]
        atomic_write(build / "config.yaml", root_config().encode())
        atomic_write(build / ".env", env_bytes(primary_secrets))
        atomic_write(build / "gateway-secondary.env", env_bytes(secondary_secrets))
        atomic_write(build / "state/profile-identity-registry.json", canonical_json(facts.pop("registry")))
        source_inputs = [
            source / "openclaw.json",
            registry_path(source, "dm"),
            registry_path(source, "group"),
        ]
        manifest = {
            "external_memory": "disabled",
            "inventory": facts,
            "profiles": profile_rows,
            "schema_version": SCHEMA,
            "skills": skill_manifest,
            "source_snapshot": {
                path.relative_to(source).as_posix(): {
                    "mtime_ns": path.stat().st_mtime_ns,
                    "sha256": sha256_file(path),
                    "size": path.stat().st_size,
                }
                for path in source_inputs
            },
        }
        validation = validate_result(build, manifest)
        if not validation["passed"]:
            raise MigrationError("validation failed: " + ",".join(validation["errors"]))
        atomic_write(build / "migration/migration-manifest.json", canonical_json(manifest))
        atomic_write(
            build / "migration/external-memory-disabled.json",
            canonical_json(
                {
                    "external_memory_provider": "",
                    "forbidden_provider_matches": [],
                    "passed": True,
                    "profiles_checked": len(profile_rows),
                    "schema_version": "hermes-external-memory-disabled/v1",
                }
            ),
        )
        os.replace(build, target)
    except Exception:
        shutil.rmtree(build, ignore_errors=True)
        raise
    return {
        "identity_routes": facts["identity_profile_count"],
        "profiles": len(profiles),
        "status": "applied",
        "target": str(target),
        "unbound_profiles": facts["unbound_profile_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    profiles, facts = inventory(args.source.resolve(strict=True))
    if not args.apply:
        print(
            json.dumps(
                {
                    "binding_count": facts["binding_count"],
                    "duplicate_binding_rows": facts["duplicate_binding_rows"],
                    "identity_routes": facts["identity_profile_count"],
                    "mismatched_binding_rows": facts["mismatched_binding_rows"],
                    "profiles": len(profiles),
                    "status": "preview",
                    "target_exists": args.target.exists() or args.target.is_symlink(),
                    "unbound_profiles": facts["unbound_profile_count"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    print(json.dumps(run(args.source, args.target), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
