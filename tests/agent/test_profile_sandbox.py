import os
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Landlock is Linux-only")


@pytest.fixture
def sandbox_tree(tmp_path):
    root = tmp_path / "hermes"
    own = root / "profiles" / "alice"
    other = root / "profiles" / "bob"
    shared = root / "shared-skills"
    for path in (own, other, shared):
        path.mkdir(parents=True)
    (own / "own.txt").write_text("own", encoding="utf-8")
    (other / "secret.txt").write_text("secret", encoding="utf-8")
    (shared / "shared.txt").write_text("shared", encoding="utf-8")
    (own / "escape-link").symlink_to(other / "secret.txt")
    return root, own, other, shared


def _run_sandbox(root, own, code):
    child_python = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable
    return subprocess.run(
        [
            child_python,
            "-m",
            "agent.profile_sandbox",
            "--root",
            str(root),
            "--profile-home",
            str(own),
            "--",
            child_python,
            "-c",
            code,
        ],
        cwd=own,
        text=True,
        capture_output=True,
        timeout=20,
        env={**os.environ, "PYTHONPATH": os.getcwd()},
    )


def test_landlock_allows_own_profile_and_read_only_shared(sandbox_tree):
    root, own, _, shared = sandbox_tree
    code = (
        "from pathlib import Path; "
        f"print(Path({str(own / 'own.txt')!r}).read_text()); "
        f"print(Path({str(shared / 'shared.txt')!r}).read_text())"
    )
    result = _run_sandbox(root, own, code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["own", "shared"]


@pytest.mark.parametrize("attack", ["direct", "concat", "symlink", "enumerate"])
def test_landlock_blocks_cross_profile_reads_and_enumeration(sandbox_tree, attack):
    root, own, other, _ = sandbox_tree
    expressions = {
        "direct": f"Path({str(other / 'secret.txt')!r}).read_text()",
        "concat": (
            f"Path({str(root)!r}, 'pro' + 'files', 'bob', 'secret.txt').read_text()"
        ),
        "symlink": f"Path({str(own / 'escape-link')!r}).read_text()",
        "enumerate": f"list(Path({str(root / 'profiles')!r}).iterdir())",
    }
    code = f"from pathlib import Path; {expressions[attack]}"
    result = _run_sandbox(root, own, code)
    assert result.returncode != 0
    assert "PermissionError" in result.stderr


def test_landlock_blocks_cross_profile_writes(sandbox_tree):
    root, own, other, _ = sandbox_tree
    code = (
        "from pathlib import Path; "
        f"Path({str(other / 'secret.txt')!r}).write_text('changed')"
    )
    result = _run_sandbox(root, own, code)
    assert result.returncode != 0
    assert (other / "secret.txt").read_text(encoding="utf-8") == "secret"


def test_landlock_allows_current_process_proc_status(sandbox_tree):
    root, own, _, _ = sandbox_tree
    result = _run_sandbox(
        root,
        own,
        "from pathlib import Path; print('Name:' in Path('/proc/self/status').read_text())",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


@pytest.mark.parametrize("magic_link", ["root", "cwd"])
def test_proc_self_magic_links_do_not_bypass_profile_isolation(
    sandbox_tree,
    magic_link,
):
    root, own, other, _ = sandbox_tree
    if magic_link == "root":
        target = "/proc/self/root" + str(other / "secret.txt")
    else:
        relative = os.path.relpath(other / "secret.txt", own)
        target = f"/proc/self/cwd/{relative}"
    result = _run_sandbox(
        root,
        own,
        f"from pathlib import Path; Path({target!r}).read_text()",
    )
    assert result.returncode != 0
    assert "PermissionError" in result.stderr
