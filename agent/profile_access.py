"""强制多 Profile 访问控制。

这个模块只在 ``HERMES_PROFILE_ISOLATION_ENFORCED=true`` 时启用。管理员身份
绑定到消息入口提供的稳定用户 ID，绝不从可写的 Profile 名称或 ``state.db``
推导。普通会话只能访问自己的 Profile；共享 Skill 和二进制目录只读。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable


_TRUE_VALUES = {"1", "true", "yes", "on"}
_PROFILE_ARGUMENT_KEYS = {
    "profile",
    "profile_id",
    "profile_name",
    "source_profile",
    "target_profile",
}
_PATH_ARGUMENT_MARKERS = (
    "path",
    "file",
    "directory",
    "cwd",
    "root",
    "source",
    "destination",
)
_SHELL_ARGUMENT_KEYS = {"command", "cmd", "script", "code"}


class ProfileAccessDenied(PermissionError):
    """当前消息主体无权访问目标 Profile。"""


def isolation_enforced() -> bool:
    return os.getenv("HERMES_PROFILE_ISOLATION_ENFORCED", "").strip().lower() in _TRUE_VALUES


def _session_value(name: str) -> str:
    try:
        from gateway.session_context import get_session_env

        return str(get_session_env(name, "") or "").strip()
    except Exception:
        return str(os.getenv(name, "") or "").strip()


def configured_admin_user_ids() -> frozenset[str]:
    raw = os.getenv("HERMES_PROFILE_ADMIN_USER_IDS", "")
    return frozenset(value.strip() for value in raw.split(",") if value.strip())


def is_profile_admin() -> bool:
    """仅按入口绑定的用户 ID 判断管理员，Profile 名称不参与授权。"""
    user_id = _session_value("HERMES_SESSION_USER_ID")
    return bool(user_id and user_id in configured_admin_user_ids())


def hermes_root() -> Path:
    from hermes_constants import get_default_hermes_root

    return get_default_hermes_root().expanduser().resolve(strict=False)


def current_profile_name() -> str:
    explicit = _session_value("HERMES_SESSION_PROFILE")
    if explicit:
        return explicit

    from hermes_constants import get_hermes_home

    root = hermes_root()
    home = get_hermes_home().expanduser().resolve(strict=False)
    try:
        relative = home.relative_to(root / "profiles")
    except ValueError:
        return "default"
    return relative.parts[0] if relative.parts else "default"


def profile_home(profile: str | None = None) -> Path:
    name = str(profile or current_profile_name()).strip()
    root = hermes_root()
    if not name or name == "default":
        return root
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
        raise ProfileAccessDenied(f"Invalid Hermes profile name: {name!r}")
    return (root / "profiles" / name).resolve(strict=False)


def authorize_profile(profile: str, *, operation: str = "read") -> None:
    """校验一个显式 Profile 参数。"""
    if not isolation_enforced() or is_profile_admin():
        return
    target = str(profile or "").strip()
    current = current_profile_name()
    if not target or target == current:
        return
    raise ProfileAccessDenied(
        f"Cross-profile {operation} denied: profile {target!r} is not the "
        f"current profile {current!r}. Only the configured administrator may "
        "operate across profiles."
    )


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def shared_read_roots(root: Path | None = None) -> tuple[Path, ...]:
    base = root or hermes_root()
    configured = os.getenv("HERMES_PROFILE_SHARED_READ_ROOTS", "").strip()
    values: Iterable[str]
    if configured:
        values = (value for value in configured.split(os.pathsep) if value)
    else:
        values = (str(base / "shared-skills"), str(base / "bin"))
    return tuple(Path(value).expanduser().resolve(strict=False) for value in values)


def authorize_path(
    path: str | os.PathLike[str],
    *,
    operation: str = "read",
    base_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """返回规范化路径；越过当前 Profile 时抛出 ``ProfileAccessDenied``。"""
    candidate = Path(os.path.expanduser(os.fspath(path)))
    if not candidate.is_absolute():
        candidate = Path(base_dir or os.getcwd()) / candidate
    resolved = candidate.resolve(strict=False)

    if not isolation_enforced() or is_profile_admin():
        return resolved

    root = hermes_root()
    if not _is_within(resolved, root):
        return resolved

    op = operation.strip().lower()
    if op == "read":
        for shared_root in shared_read_roots(root):
            if _is_within(resolved, shared_root):
                return resolved

    current = current_profile_name()
    if current != "default":
        own_home = profile_home(current)
        if _is_within(resolved, own_home):
            return resolved

    raise ProfileAccessDenied(
        f"Cross-profile {op or 'access'} denied: {resolved} is outside the "
        f"current profile {current!r}. Only the configured administrator may "
        "read, write, or enumerate another profile."
    )


def _iter_argument_checks(value: Any, key: str = ""):
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            yield from _iter_argument_checks(child_value, str(child_key).lower())
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_argument_checks(item, key)
        return
    if not isinstance(value, (str, os.PathLike)):
        return

    text = os.fspath(value)
    if key in _PROFILE_ARGUMENT_KEYS:
        yield ("profile", text)
        return

    root_text = str(hermes_root())
    path_key = any(marker in key for marker in _PATH_ARGUMENT_MARKERS)
    if path_key and text:
        yield ("path", text)

    # Shell/code arguments may contain one or more absolute paths. Extract only
    # references inside the Hermes root; ordinary prose/code content is ignored.
    if key in _SHELL_ARGUMENT_KEYS and root_text in text:
        pattern = re.compile(re.escape(root_text) + r"[^\s'\";|&<>)]*")
        for match in pattern.finditer(text):
            yield ("path", match.group(0))


def _task_base_dir(task_id: str) -> Path:
    if task_id:
        try:
            from tools.file_tools import _resolve_base_dir

            return Path(_resolve_base_dir(task_id)).resolve(strict=False)
        except Exception:
            pass
    return profile_home() / "workspace"


def get_pre_tool_call_block_message(
    tool_name: str, args: Any, *, task_id: str = ""
) -> str | None:
    """统一工具入口调用的强制策略；返回消息即拒绝本次工具调用。"""
    if not isolation_enforced() or is_profile_admin():
        return None
    if tool_name == "browser_console" and isinstance(args, dict) and args.get("expression"):
        return (
            "Profile isolation denied browser JavaScript evaluation for a "
            "non-admin session. Use browser snapshots and normal interactions."
        )
    if tool_name == "browser_navigate" and isinstance(args, dict):
        from urllib.parse import urlsplit

        scheme = urlsplit(str(args.get("url") or "")).scheme.lower()
        if scheme and scheme not in {"http", "https"}:
            return (
                "Profile isolation denied non-HTTP browser navigation for a "
                "non-admin session."
            )
    try:
        for kind, value in _iter_argument_checks(args if isinstance(args, dict) else {}):
            if kind == "profile":
                authorize_profile(value, operation=f"tool {tool_name!r}")
            else:
                authorize_path(
                    value,
                    operation="read",
                    base_dir=_task_base_dir(task_id),
                )
    except ProfileAccessDenied as exc:
        return str(exc)
    except Exception as exc:
        # 安全策略启用后，解析失败不能回落到无隔离执行。
        return f"Profile isolation policy failed closed for tool {tool_name!r}: {exc}"
    return None


def private_tmp_dir() -> Path:
    """返回当前 Profile 独占的临时目录。"""
    path = profile_home() / "cache" / "sandbox-tmp"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def private_rpc_dir() -> Path:
    """返回短路径的 Profile 独占 RPC socket 目录。"""
    path = profile_home() / ".rpc"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def sandbox_argv(argv: list[str]) -> list[str]:
    """为普通 Profile 会话生成不可逆 Landlock 启动包装。"""
    if not isolation_enforced() or is_profile_admin():
        return list(argv)
    current = current_profile_name()
    if not current or current == "default":
        raise ProfileAccessDenied(
            "Profile-isolated terminal denied: no named current profile is bound."
        )
    root = hermes_root()
    home = profile_home(current)
    expected_parent = (root / "profiles").resolve(strict=False)
    if home.parent != expected_parent:
        raise ProfileAccessDenied("Profile-isolated terminal denied: invalid profile home.")
    if sys.platform != "linux":
        raise ProfileAccessDenied(
            "Profile-isolated terminal requires Linux Landlock and cannot run unsandboxed."
        )
    return [
        sys.executable,
        str(Path(__file__).with_name("profile_sandbox.py")),
        "--root",
        str(root),
        "--profile-home",
        str(home),
        "--",
        *argv,
    ]
