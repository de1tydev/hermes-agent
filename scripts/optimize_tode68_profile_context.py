#!/usr/bin/env python3
"""压缩 TODE-68 Hermes Profiles 的常驻上下文并保留可验证回滚。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


SCHEMA = "tode68-profile-context-optimization/v1"
MEMORY_LIMIT = 4_000
USER_LIMIT = 1_200
SOUL_LIMIT = 1_600
AGENTS_LIMIT = 1_800
ALLOWED_PRIVATE_SKILLS = {
    "hermes-agent",
    "hermes-config-ops",
    "hermes-cron-ops",
}

AUTOMATION_START = re.compile(
    r"(?im)^#{1,3}\s+(?:"
    r"Promoted From Short-Term Memory(?:\s|\()|"
    r"Session:|Conversation Summary|Deep Sleep|REM Sleep|Light Sleep"
    r")"
)
HEADING = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")
DROP_SECTION = re.compile(
    r"(?i)(?:待办事项|记录时间|股市|行情|最新版本|"
    r"定时任务.*(?:修复|记录)|cron.*(?:repair|record)|"
    r"运行日志|故障记录|工作区约定|踩坑实录)"
)
DROP_LINE = re.compile(
    r"(?i)(?:"
    r"/srv/openclaw|\bopenclaw\b|openclaw-memory-promotion|"
    r"\b(?:session key|session id|message_id|job_id)\b|"
    r"untrusted metadata|\[score=[0-9.]|\brecalls=|"
    r"\bmemory_search\b|access-control|\bHooks?:|"
    r"\bASK_CODE_URL\b|\bAPI Key\b|Reference UTC|Current time:"
    r"|飞书\s*(?:open_)?id|群\s*ID|Sender ID|"
    r"访问控制规则|跨工作区.*文件|查看.*记忆文件|拦截非 Owner|"
    r"股市关注|上证指数|深证成指|创业板指|"
    r"最后更新|这是 Saber 的 curated memory|^---$|"
    r"^\s*-\s*\*{0,2}20\d{2}[-年/]|当前环境|"
    r"(?:ou_|oc_|on_)[A-Za-z0-9_-]+"
    r")"
)
USER_TEMPLATE_LINE = re.compile(
    r"(?i)(?:Learn about the person you're helping|"
    r"The more you know, the better you can help|"
    r"not building a dossier|^---$)"
)
IDENTITY_FIELD = re.compile(
    r"^-\s+\*\*(?P<key>Name|Creature|Vibe|Emoji):\*\*\s*(?P<value>.*?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WriteAction:
    path: Path
    payload: bytes
    mode: int


@dataclass(frozen=True)
class CopyTreeAction:
    source: Path
    target: Path


@dataclass
class OptimizationPlan:
    root: Path
    writes: list[WriteAction] = field(default_factory=list)
    copies: list[CopyTreeAction] = field(default_factory=list)
    removes: list[Path] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(path: Path) -> str:
    if path.is_symlink():
        raise RuntimeError(f"symlink rejected: {path}")
    if path.is_file():
        return sha256_file(path)
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise RuntimeError(f"symlink rejected: {item}")
        relative = item.relative_to(path).as_posix().encode()
        digest.update(relative)
        digest.update(b"\0")
        if item.is_file():
            digest.update(bytes.fromhex(sha256_file(item)))
    return digest.hexdigest()


def _truncate_markdown(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text + ("\n" if text else "")
    cut = text.rfind("\n\n", 0, limit)
    if cut < limit // 2:
        cut = text.rfind("\n", 0, limit)
    if cut < limit // 2:
        cut = limit
    return text[:cut].rstrip() + "\n"


def _drop_transient_sections(text: str) -> str:
    output: list[str] = []
    drop_level: int | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        heading = HEADING.match(line)
        if heading:
            level = len(heading.group("marks"))
            if drop_level is not None and level <= drop_level:
                drop_level = None
            title = heading.group("title")
            if DROP_SECTION.search(title) or (
                level >= 3 and re.fullmatch(r"20\d{2}(?:[-/.]\d{1,2}){1,2}", title)
            ):
                drop_level = level
                continue
        if drop_level is not None:
            continue
        if line.strip() == "§" or line.lstrip().startswith("<!--"):
            continue
        if DROP_LINE.search(line) or re.match(r"^\s*- \[x\]", line, re.IGNORECASE):
            continue
        if len(line) > 800:
            continue
        output.append(line)
    compact = "\n".join(output)
    compact = re.sub(r"\n{3,}", "\n\n", compact).strip()
    compact = re.sub(r"(?m)^#{2,4}\s+20\d{2}(?:[-/.]\d{1,2}){1,2}\s*\n(?=#|\Z)", "", compact)
    lines = compact.splitlines()
    pruned: list[str] = []
    for index, line in enumerate(lines):
        heading = HEADING.match(line)
        if heading:
            level = len(heading.group("marks"))
            following = next(
                (candidate for candidate in lines[index + 1 :] if candidate.strip()),
                "",
            )
            following_heading = HEADING.match(following)
            if not following or (
                following_heading and len(following_heading.group("marks")) <= level
            ):
                continue
        pruned.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(pruned)).strip()


def curate_memory(text: str, limit: int = MEMORY_LIMIT) -> str:
    marker = AUTOMATION_START.search(text)
    prefix = text[: marker.start()] if marker else text
    curated = _drop_transient_sections(prefix)
    if not curated or curated in {"# MEMORY.md", "# MEMORY.md - 长期记忆"}:
        curated = (
            "# MEMORY.md - 长期记忆\n\n"
            "仅保存已经确认、跨会话长期有效的事实；历史对话按需使用 "
            "`session_search` 或 `legacy-memory-search` 检索。"
        )
    return _truncate_markdown(curated, limit)


def curate_user(text: str, limit: int = USER_LIMIT) -> str:
    output = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if USER_TEMPLATE_LINE.search(line.strip()) or DROP_LINE.search(line):
            continue
        output.append(line)
    curated = re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip()
    if not curated:
        curated = "# USER.md"
    return _truncate_markdown(curated, limit)


def _identity(text: str, profile: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = IDENTITY_FIELD.match(line.strip())
        if not match:
            continue
        value = match.group("value").strip()
        if value:
            values[match.group("key").lower()] = value
    name = values.get("name") or (
        "群聊 Hermes Agent" if "group" in profile else "Hermes Agent"
    )
    name = re.sub(r"(?i)TODEClaw|OpenClaw", "Hermes", name)
    values["name"] = name
    values.setdefault("creature", "独立 Profile 的 AI 助手")
    values.setdefault("vibe", "直接、可靠、少说废话")
    return values


def _extract_level_two_sections(text: str, allowed: set[str]) -> str:
    sections: list[str] = []
    current: list[str] = []
    active = False
    for line in text.splitlines():
        heading = HEADING.match(line)
        if heading and len(heading.group("marks")) <= 2:
            if active and current:
                sections.append("\n".join(current).strip())
            title = heading.group("title").strip()
            active = title in allowed
            current = [line] if active else []
            continue
        if active:
            current.append(line)
    if active and current:
        sections.append("\n".join(current).strip())
    return "\n\n".join(sections)


def render_soul(profile: str, text: str) -> str:
    identity = _identity(text, profile)
    lines = [
        "# SOUL.md - Hermes Agent 身份",
        "",
        "你是当前飞书聊天的独立 Hermes Agent。只使用当前 Profile 的资料、记忆和工作区。",
        "",
        "## 行为准则",
        "",
        "- 直接解决问题，不编造结果；需要工具时先验证再回答。",
        "- 私密信息不外泄，跨 Profile 数据不读取、不引用、不写入。",
        "- 外部发送、公开发布和有破坏性的操作先取得明确确认。",
        "- 默认跟随用户语言；中文消息用中文回答。",
        "- 长任务在 `delegate_task` 可用时再委托，主线程保持可交互。",
        "",
        "## 身份",
        "",
        f"- **Name:** {identity['name']}",
        f"- **Creature:** {identity['creature']}",
        f"- **Vibe:** {identity['vibe']}",
    ]
    if identity.get("emoji"):
        lines.append(f"- **Emoji:** {identity['emoji']}")
    special = _extract_level_two_sections(text, {"任务", "输出格式"})
    if special:
        lines.extend(["", special])
    return _truncate_markdown("\n".join(lines), SOUL_LIMIT)


def render_agents(profile: str, text: str) -> str:
    lines = [
        "# AGENTS.md - Hermes Profile 工作规则",
        "",
        "## 上下文与边界",
        "",
        "- 当前 workspace 只属于当前 Profile；不得读取、枚举或修改其他 Profile。",
        "- `SOUL.md`、`USER.md`、`MEMORY.md` 已由 Hermes 自动注入，不要在启动时重复读取，也不要搜索 workspace 下不存在的 `memory/`。",
        "- 需要历史信息时，优先使用 `session_search`；旧迁移资料仅在必要时使用 `legacy-memory-search`。",
        "- Hermes 原生授权与 Landlock 是安全边界，不依赖旧文本 Hook 或旧 Owner 规则。",
        "",
        "## 执行",
        "",
        "- 先检查当前文件和运行状态，再做最小必要修改；完成后验证真实结果。",
        "- 外部发送、公开发布和破坏性操作必须先确认。",
        "- 临时文件写入当前 workspace 的 `tmp/`，最终交付写入 `deliverables/`。",
        "- 长任务只有在 `delegate_task` 可用且确有收益时才委托。",
        "",
        "## 飞书与 Lark",
        "",
        "- 定时消息使用机器人身份，不冒充用户发送。",
        "- Lark CLI 用户认证使用当前 workspace 的 `agent-data/lark-cli/` 作为 `LARKSUITE_CLI_CONFIG_DIR`；个人数据操作使用用户身份。",
    ]
    if "group" in profile:
        lines.extend(
            [
                "",
                "## 群聊",
                "",
                "- 不代表任何群成员发言；闲聊不必逐条回复，有明确价值时再介入。",
            ]
        )
    group_behavior = _extract_level_two_sections(text, {"Group Chat Behavior"})
    if group_behavior:
        lines.extend(["", group_behavior])
    if "飞书表格渲染规则" in text:
        lines.extend(
            [
                "",
                "## 飞书表格",
                "",
                "- Markdown pipe table 直接输出，不放进代码块；单卡超过 3 张表格时，若 `message` 工具可用则改走消息发送。",
            ]
        )
    if "与 Master 对话默认使用中文" in text:
        lines.extend(["", "- 与 Master 对话默认使用中文，除非当条消息明确要求其他语言。"])
    return _truncate_markdown("\n".join(lines), AGENTS_LIMIT)


def update_config(text: str) -> bytes:
    data = yaml.safe_load(text) or {}
    memory = data.setdefault("memory", {})
    memory["memory_enabled"] = True
    memory["user_profile_enabled"] = True
    memory["memory_char_limit"] = MEMORY_LIMIT
    memory["user_char_limit"] = USER_LIMIT
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode("utf-8")


def _skill_roots(skills_dir: Path) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    if not skills_dir.is_dir():
        return result
    for skill_md in skills_dir.rglob("SKILL.md"):
        if any(part.startswith(".") for part in skill_md.relative_to(skills_dir).parts):
            continue
        result.setdefault(skill_md.parent.name, []).append(skill_md.parent)
    return result


def _bundled_names(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    names = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        name, separator, _digest = line.partition(":")
        if separator and name.strip():
            names.add(name.strip())
    return names


def _mode(path: Path, fallback: int = 0o600) -> int:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return fallback


def _add_write(plan: OptimizationPlan, path: Path, payload: bytes, mode: int) -> None:
    if path.is_symlink():
        raise RuntimeError(f"symlink rejected: {path}")
    if path.is_file() and path.read_bytes() == payload:
        return
    plan.writes.append(WriteAction(path, payload, mode))


def _draft_output(
    drafts_dir: Path,
    profile: Path,
    paths: dict[str, Path],
) -> dict[str, str]:
    draft_path = drafts_dir / f"{profile.name}.json"
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    if draft.get("schema_version") != "tode68-profile-context-draft/v1":
        raise RuntimeError(f"unexpected draft schema: {draft_path}")
    if draft.get("profile") != profile.name or draft.get("model") != "deepseek-v4-flash":
        raise RuntimeError(f"draft identity mismatch: {draft_path}")
    hashes = draft.get("input_hashes") or {}
    for name, path in paths.items():
        current = sha256_file(path) if path.is_file() else None
        if hashes.get(name) != current:
            raise RuntimeError(f"draft input drift for {profile.name}/{name}")
    output = draft.get("output") or {}
    limits = {"memory": MEMORY_LIMIT, "user": USER_LIMIT, "soul": SOUL_LIMIT, "agents": AGENTS_LIMIT}
    result: dict[str, str] = {}
    for name, limit in limits.items():
        value = output.get(name)
        if not isinstance(value, str) or len(value) > limit:
            raise RuntimeError(f"invalid draft output for {profile.name}/{name}")
        if name == "agents" and not paths[name].is_file() and value.strip():
            raise RuntimeError(f"draft invented AGENTS.md for {profile.name}")
        result[name] = value.strip() + ("\n" if value.strip() else "")
    return result


def build_plan(root: Path, *, drafts_dir: Path | None = None) -> OptimizationPlan:
    root = root.resolve(strict=True)
    profiles_root = root / "profiles"
    profiles = sorted(path for path in profiles_root.iterdir() if path.is_dir())
    plan = OptimizationPlan(root=root)
    profile_rows = []

    root_config = root / "config.yaml"
    _add_write(plan, root_config, update_config(root_config.read_text(encoding="utf-8")), _mode(root_config))

    for profile in profiles:
        memory_path = profile / "memories/MEMORY.md"
        user_path = profile / "memories/USER.md"
        soul_path = profile / "SOUL.md"
        agents_path = profile / "workspace/AGENTS.md"
        config_path = profile / "config.yaml"
        context_paths = {
            "memory": memory_path,
            "user": user_path,
            "soul": soul_path,
            "agents": agents_path,
        }

        memory_before = memory_path.read_text(encoding="utf-8", errors="replace")
        user_before = user_path.read_text(encoding="utf-8", errors="replace")
        soul_before = soul_path.read_text(encoding="utf-8", errors="replace")
        if drafts_dir is not None:
            draft = _draft_output(drafts_dir, profile, context_paths)
            memory_after = draft["memory"]
            user_after = draft["user"]
            soul_after = draft["soul"]
        else:
            memory_after = curate_memory(memory_before)
            user_after = curate_user(user_before)
            soul_after = render_soul(profile.name, soul_before)
        _add_write(plan, memory_path, memory_after.encode(), _mode(memory_path))
        _add_write(plan, user_path, user_after.encode(), _mode(user_path))
        _add_write(plan, soul_path, soul_after.encode(), _mode(soul_path))
        _add_write(plan, config_path, update_config(config_path.read_text(encoding="utf-8")), _mode(config_path))

        agents_before = ""
        agents_after = ""
        if agents_path.is_file():
            agents_before = agents_path.read_text(encoding="utf-8", errors="replace")
            agents_after = (
                draft["agents"] if drafts_dir is not None else render_agents(profile.name, agents_before)
            )
            _add_write(plan, agents_path, agents_after.encode(), _mode(agents_path))

        marker = profile / ".no-bundled-skills"
        marker_payload = (
            "TODE-68 使用经审查的共享 Skill 集；不要自动灌入通用 bundled Skills。\n"
        ).encode()
        _add_write(plan, marker, marker_payload, 0o600)
        profile_rows.append(
            {
                "profile": profile.name,
                "memory_chars_before": len(memory_before),
                "memory_chars_after": len(memory_after),
                "user_chars_before": len(user_before),
                "user_chars_after": len(user_after),
                "soul_chars_before": len(soul_before),
                "soul_chars_after": len(soul_after),
                "agents_chars_before": len(agents_before),
                "agents_chars_after": len(agents_after),
            }
        )

    legacy_sources = sorted(
        path
        for path in profiles_root.glob("*/skills/legacy-memory-search")
        if path.is_dir() and not path.is_symlink()
    )
    if not legacy_sources:
        raise RuntimeError("no legacy-memory-search source found")
    legacy_digests = {tree_digest(path) for path in legacy_sources}
    if len(legacy_digests) != 1:
        raise RuntimeError("legacy-memory-search copies diverged")
    legacy_source = legacy_sources[0]
    canonical_legacy = root / "shared-skills/legacy-memory-search"
    if not canonical_legacy.exists():
        plan.copies.append(CopyTreeAction(legacy_source, canonical_legacy))
    for profile in profiles:
        target = profile / "skills/legacy-memory-search"
        if not target.exists():
            plan.copies.append(CopyTreeAction(legacy_source, target))

    shared_names = set(_skill_roots(root / "shared-skills")) | {"legacy-memory-search"}
    skill_removals: dict[str, list[str]] = {}
    for profile in profiles:
        manifest = profile / "skills/.bundled_manifest"
        bundled = _bundled_names(manifest)
        if not bundled:
            continue
        keep = shared_names | ALLOWED_PRIVATE_SKILLS
        roots = _skill_roots(profile / "skills")
        removals = []
        for name in sorted(bundled - keep):
            for skill_root in roots.get(name, []):
                if skill_root not in plan.removes:
                    plan.removes.append(skill_root)
                    removals.append(skill_root.relative_to(profile / "skills").as_posix())
        if manifest not in plan.removes:
            plan.removes.append(manifest)
        if removals:
            skill_removals[profile.name] = removals

    plan.report = {
        "schema_version": SCHEMA,
        "profiles": profile_rows,
        "summary": {
            "profile_count": len(profiles),
            "memory_chars_before": sum(row["memory_chars_before"] for row in profile_rows),
            "memory_chars_after": sum(row["memory_chars_after"] for row in profile_rows),
            "user_chars_before": sum(row["user_chars_before"] for row in profile_rows),
            "user_chars_after": sum(row["user_chars_after"] for row in profile_rows),
            "writes": len(plan.writes),
            "skill_copies": len(plan.copies),
            "skill_removals": len(plan.removes),
        },
        "skill_removals": skill_removals,
        "rewrite_source": "deepseek-v4-flash" if drafts_dir is not None else "deterministic-fallback",
    }
    return plan


def _atomic_write(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.context-opt-", dir=path.parent)
    tmp = Path(raw)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _copy_tree_atomic(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        raise RuntimeError(f"copy target already exists: {target}")
    staging = target.with_name(f".{target.name}.context-opt-{os.getpid()}")
    if staging.exists():
        raise RuntimeError(f"staging collision: {staging}")
    shutil.copytree(source, staging, symlinks=False)
    os.replace(staging, target)


def _copy_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, symlinks=False)
    else:
        shutil.copy2(source, target, follow_symlinks=False)


def _make_tree_owner_writable(path: Path) -> None:
    if not path.exists() or path.is_symlink():
        return
    items = [path, *path.rglob("*")] if path.is_dir() else [path]
    for item in items:
        if item.is_symlink():
            raise RuntimeError(f"symlink rejected during restore: {item}")
        mode = stat.S_IMODE(item.stat().st_mode)
        if item.is_dir():
            os.chmod(item, mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        else:
            os.chmod(item, mode | stat.S_IRUSR | stat.S_IWUSR)


def _target_record(root: Path, target: Path) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": target.relative_to(root).as_posix(),
        "existed": target.exists() or target.is_symlink(),
    }
    if record["existed"]:
        if target.is_symlink():
            raise RuntimeError(f"symlink rejected: {target}")
        st = target.stat()
        record.update(
            {
                "kind": "directory" if target.is_dir() else "file",
                "sha256": tree_digest(target),
                "mode": stat.S_IMODE(st.st_mode),
                "uid": st.st_uid,
                "gid": st.st_gid,
            }
        )
    return record


def apply_plan(
    plan: OptimizationPlan,
    *,
    backup_dir: Path,
    expected_uid: int | None = None,
) -> dict[str, Any]:
    root = plan.root
    backup_dir = backup_dir.resolve(strict=False)
    if backup_dir.exists() or backup_dir.is_symlink():
        raise RuntimeError(f"backup target exists: {backup_dir}")
    backup_dir.mkdir(parents=True, mode=0o700)
    originals = backup_dir / "original"

    targets = [action.path for action in plan.writes]
    targets += [action.target for action in plan.copies]
    targets += plan.removes
    unique_targets: list[Path] = []
    seen = set()
    for target in targets:
        resolved_parent = target.parent.resolve(strict=True)
        resolved = resolved_parent / target.name
        resolved.relative_to(root)
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            unique_targets.append(target)

    records = []
    for target in unique_targets:
        record = _target_record(root, target)
        if record["existed"] and expected_uid is not None:
            relative = record["path"]
            if relative.startswith("profiles/") and record["uid"] != expected_uid:
                raise RuntimeError(f"unexpected Profile owner for {relative}: {record['uid']}")
        records.append(record)
        if record["existed"]:
            _copy_backup(target, originals / record["path"])

    before = {
        "schema_version": SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "targets": records,
        "report": plan.report,
    }
    _atomic_write(
        backup_dir / "before.json",
        (json.dumps(before, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
        0o600,
    )

    for action in plan.copies:
        _copy_tree_atomic(action.source, action.target)
    for action in plan.writes:
        _atomic_write(action.path, action.payload, action.mode)
    for target in sorted(plan.removes, key=lambda path: len(path.parts), reverse=True):
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)

    receipt = {
        "schema_version": SCHEMA,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "backup": str(backup_dir),
        "report": plan.report,
        "post_apply": [
            {
                "path": target.relative_to(root).as_posix(),
                "exists": target.exists(),
                "sha256": tree_digest(target) if target.exists() else None,
            }
            for target in unique_targets
        ],
    }
    _atomic_write(
        backup_dir / "receipt.json",
        (json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
        0o600,
    )
    return receipt


def restore_backup(root: Path, backup_dir: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    backup_dir = backup_dir.resolve(strict=True)
    before = json.loads((backup_dir / "before.json").read_text(encoding="utf-8"))
    if before.get("schema_version") != SCHEMA or before.get("root") != str(root):
        raise RuntimeError("backup metadata does not match this root")
    restored = []
    shared_skills = root / "shared-skills"
    shared_mode = stat.S_IMODE(shared_skills.stat().st_mode) if shared_skills.exists() else None
    if shared_mode is not None:
        os.chmod(shared_skills, shared_mode | stat.S_IWUSR | stat.S_IXUSR)
    try:
        for record in sorted(before["targets"], key=lambda row: row["path"].count("/"), reverse=True):
            target = root / record["path"]
            target.resolve(strict=False).relative_to(root)
            if target.exists():
                _make_tree_owner_writable(target)
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            if record["existed"]:
                _copy_backup(backup_dir / "original" / record["path"], target)
                os.chmod(target, int(record["mode"]))
            restored.append(record["path"])
    finally:
        if shared_mode is not None:
            os.chmod(shared_skills, shared_mode)
    return {"status": "restored", "backup": str(backup_dir), "targets": restored}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--expected-uid", type=int)
    parser.add_argument("--draft-dir", type=Path)
    parser.add_argument("--restore", type=Path)
    args = parser.parse_args()
    if args.restore:
        print(json.dumps(restore_backup(args.root, args.restore), ensure_ascii=False, indent=2))
        return 0
    plan = build_plan(
        args.root,
        drafts_dir=args.draft_dir.resolve(strict=True) if args.draft_dir else None,
    )
    if not args.apply:
        print(json.dumps(plan.report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.backup_dir is None:
        parser.error("--backup-dir is required with --apply")
    receipt = apply_plan(plan, backup_dir=args.backup_dir, expected_uid=args.expected_uid)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
