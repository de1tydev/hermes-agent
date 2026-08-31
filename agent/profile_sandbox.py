"""以 Linux Landlock 限制普通 Profile 的终端及全部子进程。"""

from __future__ import annotations

import argparse
import ctypes
import errno
import os
import sys
from pathlib import Path


_SYS_LANDLOCK_CREATE_RULESET = 444
_SYS_LANDLOCK_ADD_RULE = 445
_SYS_LANDLOCK_RESTRICT_SELF = 446
_LANDLOCK_CREATE_RULESET_VERSION = 1
_LANDLOCK_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

_EXECUTE = 1 << 0
_WRITE_FILE = 1 << 1
_READ_FILE = 1 << 2
_READ_DIR = 1 << 3
_REMOVE_DIR = 1 << 4
_REMOVE_FILE = 1 << 5
_MAKE_CHAR = 1 << 6
_MAKE_DIR = 1 << 7
_MAKE_REG = 1 << 8
_MAKE_SOCK = 1 << 9
_MAKE_FIFO = 1 << 10
_MAKE_BLOCK = 1 << 11
_MAKE_SYM = 1 << 12
_REFER = 1 << 13
_TRUNCATE = 1 << 14

_READ_EXEC = _EXECUTE | _READ_FILE | _READ_DIR
_READ_WRITE = (
    _READ_EXEC
    | _WRITE_FILE
    | _REMOVE_DIR
    | _REMOVE_FILE
    | _MAKE_CHAR
    | _MAKE_DIR
    | _MAKE_REG
    | _MAKE_SOCK
    | _MAKE_FIFO
    | _MAKE_BLOCK
    | _MAKE_SYM
    | _REFER
    | _TRUNCATE
)


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int),
    ]


def _syscall(libc, number: int, *args) -> int:
    result = int(libc.syscall(number, *args))
    if result < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return result


def landlock_abi() -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        return _syscall(
            libc,
            _SYS_LANDLOCK_CREATE_RULESET,
            ctypes.c_void_p(),
            ctypes.c_size_t(0),
            ctypes.c_uint(_LANDLOCK_CREATE_RULESET_VERSION),
        )
    except OSError as exc:
        if exc.errno in {errno.ENOSYS, errno.EOPNOTSUPP, errno.EINVAL}:
            return 0
        raise


def _handled_rights(abi: int) -> int:
    rights = _READ_WRITE
    if abi < 2:
        rights &= ~_REFER
    if abi < 3:
        rights &= ~_TRUNCATE
    return rights


def _add_path_rule(libc, ruleset_fd: int, path: Path, allowed: int, handled: int) -> None:
    if not path.exists():
        return
    real = path.resolve(strict=True)
    flags = getattr(os, "O_PATH", os.O_RDONLY) | os.O_CLOEXEC
    parent_fd = os.open(real, flags)
    try:
        attr = _PathBeneathAttr(allowed_access=allowed & handled, parent_fd=parent_fd)
        _syscall(
            libc,
            _SYS_LANDLOCK_ADD_RULE,
            ctypes.c_int(ruleset_fd),
            ctypes.c_int(_LANDLOCK_RULE_PATH_BENEATH),
            ctypes.byref(attr),
            ctypes.c_uint(0),
        )
    finally:
        os.close(parent_fd)


def apply_profile_landlock(root: Path, profile_home: Path) -> None:
    root = root.resolve(strict=True)
    profile_home = profile_home.resolve(strict=True)
    profiles_root = (root / "profiles").resolve(strict=True)
    if profile_home.parent != profiles_root:
        raise RuntimeError("profile home must be an immediate child of <root>/profiles")

    abi = landlock_abi()
    if abi < 1:
        raise RuntimeError("Linux Landlock is unavailable; refusing unsandboxed execution")
    handled = _handled_rights(abi)
    libc = ctypes.CDLL(None, use_errno=True)
    attr = _RulesetAttr(handled_access_fs=handled)
    ruleset_fd = _syscall(
        libc,
        _SYS_LANDLOCK_CREATE_RULESET,
        ctypes.byref(attr),
        ctypes.sizeof(attr),
        ctypes.c_uint(0),
    )
    try:
        # 当前 Profile 是唯一可写业务树。
        _add_path_rule(libc, ruleset_fd, profile_home, _READ_WRITE, handled)

        # 系统运行时和 Hermes 程序只读/可执行。
        for path in (
            Path("/bin"),
            Path("/sbin"),
            Path("/usr"),
            Path("/lib"),
            Path("/lib64"),
            Path("/etc"),
            Path("/opt/hermes"),
            Path("/sys"),
            root / "shared-skills",
            root / "bin",
            # Self-contained managed runtimes (including CoreCLR) inspect
            # their own process mappings during startup.  Resolve the magic
            # link now so the rule covers only this sandbox process, never
            # the global /proc tree or another process.
            Path("/proc/self"),
        ):
            _add_path_rule(libc, ruleset_fd, path, _READ_EXEC, handled)

        # 只开放常规字符设备，不开放 /proc 或整个 /dev。
        for device in (
            "/dev/null",
            "/dev/zero",
            "/dev/random",
            "/dev/urandom",
            "/dev/tty",
            "/dev/ptmx",
            "/dev/full",
        ):
            _add_path_rule(
                libc,
                ruleset_fd,
                Path(device),
                _READ_FILE | _WRITE_FILE,
                handled,
            )

        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        _syscall(
            libc,
            _SYS_LANDLOCK_RESTRICT_SELF,
            ctypes.c_int(ruleset_fd),
            ctypes.c_uint(0),
        )
    finally:
        os.close(ruleset_fd)


def _parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", required=True)
    parser.add_argument("--profile-home", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("a command is required after --")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root)
    home = Path(args.profile_home)

    # 避免所有 Profile 共用 /tmp；多数工具会遵循 TMPDIR/TMP/TEMP。
    private_tmp = home / "cache" / "sandbox-tmp"
    private_tmp.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ["TMPDIR"] = str(private_tmp)
    os.environ["TMP"] = str(private_tmp)
    os.environ["TEMP"] = str(private_tmp)

    try:
        apply_profile_landlock(root, home)
    except Exception as exc:
        sys.stderr.write(f"Profile sandbox refused to start: {exc}\n")
        return 126

    os.execvpe(args.command[0], args.command, os.environ)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
