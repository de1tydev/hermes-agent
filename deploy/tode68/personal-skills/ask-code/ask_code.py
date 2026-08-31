#!/usr/bin/env python3
"""Ask Code — caller-side CLI.

Reads `ASK_CODE_URL`, `ASK_CODE_API_KEY`, and (optionally) `ASK_CODE_CALLER`
from `.env` next to this script or from the process environment. By default it
submits a question to the Ask Code gateway and returns a job_id immediately so
Hermes does not block the current turn while the answering engine runs. Use
`--wait` for short/local synchronous calls or `--status JOB_ID` to check a
previously submitted job.

Stage-2 API contract:

    POST /ask
    {
      "project":    "teap",            # required
      "question":   "...",             # required
      "caller":     "liao",            # required, identifies the real operator
      "components": ["backend"],       # optional, omit to use project default
      "refs":       {"backend": "develop"}, # optional, omit to use repo default
      "engine":     "pi",              # optional, server default if omitted
      "mode":       "debug",           # optional, "qa" (default) or "debug"
      "exec":       true,              # optional, debug-only reproduction tier
      "exec_budget_s": 7200,           # optional, only with exec
      "simulation_mode": "mid_term",   # required with exec; canonical execution mode
      "session_id": "9f3c..."          # optional follow-up (--session); omit on the first ask
    }

Refs MUST be passed as structured key=value entries (one per --ref), not as
free text inside the question. The CLI does not implement any legacy
``--scope`` / ``--branch-*`` flags; the server returns 400 for those.

Debug mode (`--mode debug`) asks for a root-cause report instead of a
concept-level explanation, and may carry capture/log attachments
(`--attach PATH`, repeatable; `--attach-kind` overrides the kind inferred from
the suffix). A request WITH attachments is sent as multipart/form-data — a
`payload` field holding the JSON body above, an `attachments_meta` field with
one `{name, kind}` entry per file, and the file parts in that same order. A
request WITHOUT attachments stays plain JSON, byte-identical to a qa call.
Debug mode must be enabled for the API key AND the project on the server; it is
refused with 400/403 otherwise.

Exec (`--exec`, implies `--mode debug`) is the reproduction tier of debug mode:
the sandbox gets enough CPU/RAM/wall-clock to actually RUN the project's cases,
so the report can be built on a real stack trace instead of a reading of the
code. It discloses no more than a debug turn does. `--exec-budget-s N` declares
a wall-clock budget; the server clamps it into its own configured range rather
than refusing it. Exec needs a third set of switches on the server (flag + key
+ project) and answers 400/403 without them. Exec turns run in a separate,
narrow queue and take minutes to hours: submit asynchronously and poll with
`--status` rather than holding a chat lane with `--wait`.
Every exec request also requires `--simulation-mode NAME`; the value is sent
as structured metadata and frozen in the session so the answering agent never
guesses a mode from ambiguous wording in the question.

Sessions: the first accepted ask opens a followable session and the server
returns `session_id` alongside `job_id`. The easy path is `--follow-up`: the
CLI remembers the most recent session per (gateway URL, project) in
`.ask_code_state.json` next to this script (session metadata only — never
questions, answers, or keys) and reuses it automatically, transparently
starting a fresh session when the saved one has expired. `--session
SESSION_ID` is the strict variant: it never auto-recovers. `--session-info
SESSION_ID` / `--end-session SESSION_ID` inspect / close a session without
submitting a new question. Set `ASK_CODE_STATE_FILE` to relocate the state
file (mainly for tests).

Exit codes:
  0  done (answer printed), or not done yet (queued/running progress printed)
  2  blocked (input or output guard refused; reason on stderr)
  3  error (server-side error; reason on stderr)
  4  cancelled / outer timeout
  5  session unavailable (409 turn in flight; or 410 expired/closed with
     explicit --session — re-ask without --session to start fresh)
  64 usage / configuration error (including a locally rejected --attach:
     missing/unreadable file, over the per-file or total size limit, too many
     files, or --attach without --mode debug; and --exec with an explicit
     --mode qa, or --exec-budget-s without exec)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Disclosure modes + attachment limits
#
# The limits mirror the server defaults (ASK_CODE_DEBUG_MAX_*). They exist here
# only so an oversized upload fails locally, before a single byte crosses the
# network; the server enforces its own configured values regardless.
# ---------------------------------------------------------------------------

MODES = ("qa", "debug")
_SIMULATION_MODE_RE = re.compile(r"\A[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
ATTACHMENT_KINDS = ("tc", "tr", "log", "zip", "text")
MAX_ATTACHMENTS = 8
MAX_ATTACHMENT_BYTES = 104_857_600  # 100 MiB per file
MAX_TOTAL_ATTACHMENT_BYTES = 268_435_456  # 256 MiB per request
_KIND_BY_SUFFIX = {
    ".tc": "tc",
    ".tr": "tr",
    ".log": "log",
    ".zip": "zip",
}
_UPLOAD_CHUNK = 1 << 20

# Polling. `_DEFAULT_POLL_S` is the historical first interval; a response that
# carries `poll_after_s` (the server hands one to every accepted job, and
# repeats it on a still-running exec job) wins over it, bounded by
# `_MAX_POLL_S` so a bogus value cannot park the loop for a day. The server's
# own exec cadence is NOT mirrored here — it is read off the wire.
_DEFAULT_POLL_S = 5.0
_MAX_POLL_S = 300.0


def _load_dotenv() -> None:
    env_path = SKILL_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


# ---------------------------------------------------------------------------
# Local session memory (.ask_code_state.json)
#
# Stores ONLY session metadata — session_id, project, url, engine,
# created/updated timestamps, session_expires_at — never questions, answers,
# or API keys. It is a convenience cache for --follow-up: corruption or
# absence must never break the CLI.
# ---------------------------------------------------------------------------


def _state_file() -> Path:
    override = os.environ.get("ASK_CODE_STATE_FILE")
    return Path(override) if override else SKILL_DIR / ".ask_code_state.json"


def _load_state() -> dict:
    try:
        raw = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _drop_saved_session_id(session_id: str) -> None:
    """Forget every memo entry pointing at ``session_id``. Never raises.

    Used when a session is known dead (410 seen during --follow-up recovery)
    or explicitly ended (--end-session), so the next --follow-up starts fresh
    instead of bouncing through another recovery round-trip.
    """
    try:
        state = _load_state()
        sessions = state.get("sessions")
        if not isinstance(sessions, dict):
            return
        changed = False
        for by_project in list(sessions.values()):
            if not isinstance(by_project, dict):
                continue
            for project, entry in list(by_project.items()):
                if isinstance(entry, dict) and entry.get("session_id") == session_id:
                    del by_project[project]
                    changed = True
        if changed:
            _write_state_atomically(state)
    except Exception:  # noqa: BLE001
        pass


def _saved_session(url: str, project: str) -> dict | None:
    sessions = _load_state().get("sessions")
    if not isinstance(sessions, dict):
        return None
    by_project = sessions.get(url)
    if not isinstance(by_project, dict):
        return None
    entry = by_project.get(project)
    if isinstance(entry, dict) and entry.get("session_id"):
        return entry
    return None


def _save_session(
    url: str,
    project: str,
    session_id: str,
    engine: str | None,
    session_expires_at: float | None,
    mode: str = "qa",
    exec_tier: bool = False,
    simulation_mode: str | None = None,
) -> None:
    """Remember the most recent session for (url, project). Never raises.

    `mode` is written only for a debug session and `exec`/`simulation_mode`
    only for an exec one: a qa memo entry keeps exactly the fields it had
    before debug mode existed, and a missing key reads back as qa / non-exec
    (see `_resolve_mode` and `_resolve_exec`). The debug-exec fields belong in
    the memo because they are frozen into the session's binding server-side;
    the CLI must replay them for a convenient follow-up.
    """
    try:
        state = _load_state()
        sessions = state.setdefault("sessions", {})
        if not isinstance(sessions, dict):
            sessions = {}
            state["sessions"] = sessions
        by_project = sessions.setdefault(url, {})
        if not isinstance(by_project, dict):
            by_project = {}
            sessions[url] = by_project
        previous = by_project.get(project)
        now = time.time()
        created_at = now
        if isinstance(previous, dict) and previous.get("session_id") == session_id:
            created_at = previous.get("created_at") or now
        entry: dict = {
            "session_id": session_id,
            "project": project,
            "url": url,
            "engine": engine,
            "created_at": created_at,
            "updated_at": now,
        }
        if session_expires_at is not None:
            entry["session_expires_at"] = session_expires_at
        if mode == "debug":
            entry["mode"] = mode
        if exec_tier:
            entry["exec"] = True
            entry["simulation_mode"] = simulation_mode
        by_project[project] = entry
        _write_state_atomically(state)
    except OSError:
        # Losing the memo only costs convenience; the submit already worked.
        pass


def _write_state_atomically(state: dict) -> None:
    import tempfile

    target = _state_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, sort_keys=True, indent=2)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _http_json(
    method: str,
    url: str,
    api_key: str,
    body: dict | None = None,
) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            payload = {"error": exc.reason}
        return exc.code, payload


# ---------------------------------------------------------------------------
# Attachments (debug mode only)
#
# One owner for the whole attachment contract: how --attach/--attach-kind pair
# up, which kind a suffix implies, the local size/count prechecks, and the
# multipart encoding the gateway parses. Nothing else in this file re-derives
# any of it.
# ---------------------------------------------------------------------------


class _AttachmentError(Exception):
    """A locally detectable problem with --attach; never reaches the network."""


class _AttachAction(argparse.Action):
    """Append one pending attachment to the ordered `attachments` list."""

    def __call__(self, parser, namespace, values, option_string=None):
        items = list(getattr(namespace, "attachments", None) or [])
        items.append({"path": values, "kind": None})
        namespace.attachments = items


class _AttachKindAction(argparse.Action):
    """Set the kind of the --attach that precedes it (positional pairing)."""

    def __call__(self, parser, namespace, values, option_string=None):
        items = list(getattr(namespace, "attachments", None) or [])
        if not items:
            raise argparse.ArgumentError(
                self, "--attach-kind must follow the --attach it describes"
            )
        if items[-1]["kind"] is not None:
            raise argparse.ArgumentError(
                self,
                "--attach-kind is already set for the preceding "
                f"--attach {items[-1]['path']!r}",
            )
        if values not in ATTACHMENT_KINDS:
            raise argparse.ArgumentError(
                self, f"--attach-kind must be one of {', '.join(ATTACHMENT_KINDS)}"
            )
        items[-1]["kind"] = values
        namespace.attachments = items


def _infer_kind(path: Path) -> str:
    """Map a suffix to an attachment kind; anything unknown is plain text."""
    return _KIND_BY_SUFFIX.get(path.suffix.lower(), "text")


def _resolve_attachments(pending: list[dict]) -> list[dict]:
    """Validate --attach entries locally and return [{name, kind, path, bytes}].

    Everything checkable without the server is checked here — existence,
    regular-file-ness, readability, per-file size, total size, count — so an
    oversized capture fails in milliseconds instead of after a 100 MB upload.
    """
    if not pending:
        return []
    if len(pending) > MAX_ATTACHMENTS:
        raise _AttachmentError(
            f"at most {MAX_ATTACHMENTS} attachments per request "
            f"(got {len(pending)})"
        )
    resolved: list[dict] = []
    total = 0
    for item in pending:
        path = Path(item["path"]).expanduser()
        if not path.exists():
            raise _AttachmentError(f"attachment not found: {item['path']}")
        if not path.is_file():
            raise _AttachmentError(f"attachment is not a regular file: {item['path']}")
        if not os.access(path, os.R_OK):
            raise _AttachmentError(f"attachment is not readable: {item['path']}")
        size = path.stat().st_size
        if size == 0:
            raise _AttachmentError(f"attachment is empty: {item['path']}")
        if size > MAX_ATTACHMENT_BYTES:
            raise _AttachmentError(
                f"attachment exceeds the {MAX_ATTACHMENT_BYTES} byte per-file "
                f"limit ({size} bytes): {item['path']}"
            )
        total += size
        if total > MAX_TOTAL_ATTACHMENT_BYTES:
            raise _AttachmentError(
                f"attachments exceed the {MAX_TOTAL_ATTACHMENT_BYTES} byte "
                "total limit for one request"
            )
        resolved.append(
            {
                "name": path.name,
                "kind": item["kind"] or _infer_kind(path),
                "path": path,
                "bytes": size,
            }
        )
    return resolved


def _multipart_parts(body: dict, attachments: list[dict], boundary: str):
    """Yield (header_bytes, file_path_or_None, declared_size) in wire order.

    Field order is the gateway's contract: the `payload` JSON, then
    `attachments_meta`, then one `files` part per attachment IN THE SAME ORDER
    as the meta entries (the server zips the two together).
    """
    marker = f"--{boundary}\r\n".encode()

    def _field(name: str, value: str) -> bytes:
        # No filename => the gateway's parser reads this part as a form FIELD
        # (a str), which is what `payload` / `attachments_meta` must be. A
        # filename on any part makes it an uploaded FILE instead.
        return (
            marker
            + f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            + value.encode("utf-8")
            + b"\r\n"
        )

    yield _field("payload", json.dumps(body)), None, 0
    if not attachments:
        return
    meta = [{"name": a["name"], "kind": a["kind"]} for a in attachments]
    yield _field("attachments_meta", json.dumps(meta)), None, 0
    for attachment in attachments:
        header = (
            marker
            + (
                'Content-Disposition: form-data; name="files"; '
                f'filename="{attachment["name"]}"\r\n'
            ).encode()
            + b"Content-Type: application/octet-stream\r\n\r\n"
        )
        yield header, attachment["path"], attachment["bytes"]


def _encode_multipart(
    body: dict, attachments: list[dict]
) -> tuple[str, int, object]:
    """Return (content_type, content_length, body_iterable).

    The length is computed up front from the headers plus each file's stat
    size: the gateway REFUSES a multipart /ask without a Content-Length (a
    chunked upload has no declared bound), so streaming from disk is only
    possible with the size known in advance.
    """
    boundary = "askcode" + secrets.token_hex(16)
    parts = list(_multipart_parts(body, attachments, boundary))
    closer = f"--{boundary}--\r\n".encode()
    length = sum(len(header) + size + (2 if path else 0) for header, path, size in parts)
    length += len(closer)

    def _iter_body():
        for header, path, size in parts:
            yield header
            if path is None:
                continue
            sent = 0
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(_UPLOAD_CHUNK)
                    if not chunk:
                        break
                    sent += len(chunk)
                    if sent > size:
                        raise _AttachmentError(
                            f"attachment grew while being uploaded: {path}"
                        )
                    yield chunk
            if sent != size:
                raise _AttachmentError(
                    f"attachment changed size while being uploaded: {path}"
                )
            yield b"\r\n"
        yield closer

    return f"multipart/form-data; boundary={boundary}", length, _iter_body()


def _http_multipart(
    url: str, api_key: str, body: dict, attachments: list[dict]
) -> tuple[int, dict]:
    """POST one /ask request whose attachments stream straight off disk."""
    content_type, length, stream = _encode_multipart(body, attachments)
    req = urllib.request.Request(url, data=stream, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", content_type)
    # Explicit, so urllib does not fall back to chunked transfer encoding.
    req.add_header("Content-Length", str(length))
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            payload = {"error": exc.reason}
        return exc.code, payload


def _submit_ask(
    url: str, api_key: str, body: dict, attachments: list[dict]
) -> tuple[int, dict]:
    """One submit. Attachments pick the transport; nothing else changes."""
    if attachments:
        return _http_multipart(f"{url}/ask", api_key, body, attachments)
    return _http_json("POST", f"{url}/ask", api_key, body)


def _resolve_mode(
    explicit: str | None, saved: dict | None, exec_requested: bool = False
) -> str:
    """Pick this request's disclosure mode.

    An explicit --mode always wins (a mismatch against a session's frozen mode
    is the server's 400 to give) — including the contradictory `--mode qa
    --exec`, which the caller-side precheck then refuses. Otherwise --exec
    implies debug, since exec is the reproduction tier OF debug mode. Failing
    that, a continued session replays the mode recorded in its memo, so
    following up on a debug conversation does not need the flag repeated.
    Everything else is qa.
    """
    if explicit is not None:
        return explicit
    if exec_requested:
        return "debug"
    if saved and saved.get("mode") in MODES:
        return str(saved["mode"])
    return "qa"


def _resolve_exec(explicit: bool, mode: str, saved: dict | None) -> bool:
    """Pick this request's resource tier (the exec half of the binding).

    `--exec` always wins. Otherwise a continued session replays the tier
    recorded in its memo, so following up on a reproduction session does not
    need the flag repeated — but only while the resolved mode is still debug:
    an explicit `--mode qa` against an exec memo is the caller contradicting
    themselves, and the server's mode 400 is the honest answer to give them
    (rather than a local error about a flag they never typed).
    """
    if explicit:
        return True
    if mode != "debug":
        return False
    return bool(saved and saved.get("exec") is True)


def _parse_simulation_mode(value: str) -> str:
    if _SIMULATION_MODE_RE.fullmatch(value) is None:
        raise argparse.ArgumentTypeError(
            "simulation mode must start with a letter and contain only "
            "letters, digits, '_' or '-' (max 64 characters)"
        )
    return value


def _resolve_simulation_mode(
    explicit: str | None, exec_tier: bool, saved: dict | None
) -> str | None:
    if explicit is not None:
        return explicit
    if not exec_tier:
        return None
    saved_mode = saved.get("simulation_mode") if saved else None
    return str(saved_mode) if isinstance(saved_mode, str) and saved_mode else None


def _poll_interval(value: object, fallback: float) -> float:
    """A server-declared `poll_after_s`, sanity-bounded; else `fallback`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    if value <= 0:
        return fallback
    return min(float(value), _MAX_POLL_S)


def _parse_ref(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            f"--ref expects key=value (got {value!r})"
        )
    key, ref = value.split("=", 1)
    key = key.strip()
    ref = ref.strip()
    if not key or not ref:
        raise argparse.ArgumentTypeError(
            f"--ref expects non-empty key and value (got {value!r})"
        )
    return key, ref


def _build_request_body(
    args: argparse.Namespace,
    session_id: str | None = None,
    mode: str = "qa",
    exec_tier: bool = False,
    simulation_mode: str | None = None,
) -> dict:
    body: dict = {
        "project": args.project,
        "question": args.question,
        "caller": args.caller,
    }
    # qa is the server default, so a qa body stays exactly what it was before
    # debug mode existed — no new key on the wire for existing callers. Same
    # rule for the exec tier: absent means the analysis tier, so a non-exec
    # body gains no key either.
    if mode != "qa":
        body["mode"] = mode
    if exec_tier:
        body["exec"] = True
        body["simulation_mode"] = simulation_mode
        if args.exec_budget_s is not None:
            body["exec_budget_s"] = args.exec_budget_s
    if args.component:
        body["components"] = list(args.component)
    if args.ref:
        refs: dict[str, str] = {}
        for key, ref in args.ref:
            if key in refs and refs[key] != ref:
                raise SystemExit(
                    f"ask-code: conflicting --ref for {key!r}: "
                    f"{refs[key]!r} vs {ref!r}"
                )
            refs[key] = ref
        body["refs"] = refs
    if args.engine:
        body["engine"] = args.engine
    if session_id:
        body["session_id"] = session_id
    return body


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(prog="ask-code")
    parser.add_argument("question", nargs="?", help="The question to ask about the project.")
    parser.add_argument(
        "--project",
        default=os.environ.get("ASK_CODE_PROJECT"),
        help="Project ID (e.g. 'teap'). Falls back to ASK_CODE_PROJECT.",
    )
    parser.add_argument(
        "--caller",
        default=os.environ.get("ASK_CODE_CALLER"),
        help=(
            "Real operator identifier. Required by the server. "
            "Falls back to ASK_CODE_CALLER."
        ),
    )
    parser.add_argument(
        "--component",
        action="append",
        default=None,
        help=(
            "Component to include (repeatable). Omit to use the project's "
            "default_components."
        ),
    )
    parser.add_argument(
        "--ref",
        action="append",
        default=None,
        type=_parse_ref,
        metavar="KEY=REF",
        help=(
            "Component or repo ref override, e.g. backend=develop. "
            "Repeat to set multiple. Omit to use repo default_ref."
        ),
    )
    parser.add_argument("--engine", default=None, help="pi | codex")
    parser.add_argument(
        "--mode",
        choices=MODES,
        default=None,
        help=(
            "Disclosure profile. 'qa' (default) returns a concept-level "
            "explanation; 'debug' returns a root-cause report that may quote "
            "short located code excerpts, and accepts --attach. Debug must be "
            "enabled for this key AND project on the server. Omitted on a "
            "--follow-up, the continued session's own mode is reused."
        ),
    )
    parser.add_argument(
        "--exec",
        action="store_true",
        help=(
            "Reproduction tier of debug mode: implies --mode debug and lets "
            "the sandboxed agent actually run the project's cases (16C/32G, "
            "hours of wall clock, its own queue). Must be enabled for this key "
            "AND project on the server. Discloses no more than a debug turn. "
            "Omitted on a --follow-up, an exec session's own tier is reused."
        ),
    )
    parser.add_argument(
        "--exec-budget-s",
        type=int,
        default=None,
        metavar="SECONDS",
        help=(
            "Wall-clock budget for an --exec turn. The server clamps it into "
            "its own configured range instead of refusing it, so ask for what "
            "the run needs. Requires exec."
        ),
    )
    parser.add_argument(
        "--simulation-mode",
        type=_parse_simulation_mode,
        default=None,
        metavar="NAME",
        help=(
            "Canonical execution mode for a reproduction turn, for example "
            "mid_term. Required with --exec, saved in the session memo, and "
            "reused by --follow-up instead of being inferred from the question."
        ),
    )
    parser.add_argument(
        "--attach",
        action=_AttachAction,
        metavar="PATH",
        dest="attachments",
        default=None,
        help=(
            "Attach a capture/log file to a debug request (repeatable, max "
            f"{MAX_ATTACHMENTS}). Kind is inferred from the suffix "
            "(.tc/.tr/.log/.zip, anything else text); override it with a "
            "following --attach-kind. Requires --mode debug."
        ),
    )
    parser.add_argument(
        "--attach-kind",
        action=_AttachKindAction,
        metavar="KIND",
        dest="attachments",
        default=None,
        help=(
            f"Kind ({'|'.join(ATTACHMENT_KINDS)}) for the --attach immediately "
            "before it."
        ),
    )
    parser.add_argument(
        "--session",
        metavar="SESSION_ID",
        default=None,
        help=(
            "Follow up an existing Ask Code session instead of starting a new "
            "one. project/components/refs/engine must be omitted or exactly "
            "match the session; the server rejects any other value. Strict: "
            "an expired/closed session is reported (exit 5), never retried."
        ),
    )
    parser.add_argument(
        "--follow-up",
        action="store_true",
        help=(
            "Continue the most recent saved session for this gateway+project "
            "(remembered in .ask_code_state.json). Starts a fresh session "
            "automatically when nothing is saved or the saved session has "
            "expired. Mutually exclusive with --session."
        ),
    )
    parser.add_argument("--timeout-s", type=int, default=900)
    parser.add_argument("--wait", action="store_true", help="Poll until completion. Default is async submit only.")
    parser.add_argument("--status", metavar="JOB_ID", help="Check one existing Ask Code job and exit.")
    parser.add_argument("--cancel", metavar="JOB_ID", help="Cancel one existing Ask Code job and exit.")
    parser.add_argument(
        "--session-info",
        metavar="SESSION_ID",
        help="Fetch metadata for one existing Ask Code session and exit.",
    )
    parser.add_argument(
        "--end-session",
        metavar="SESSION_ID",
        help="Close one existing Ask Code session and exit.",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("ASK_CODE_URL"),
        help="Override ASK_CODE_URL.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the JSON body that would be POSTed and exit.",
    )
    args = parser.parse_args(argv)

    url = (args.url or "").rstrip("/")
    api_key = os.environ.get("ASK_CODE_API_KEY", "")
    if not url:
        print("ask-code: ASK_CODE_URL is not set", file=sys.stderr)
        return 64
    if not api_key:
        print("ask-code: ASK_CODE_API_KEY is not set", file=sys.stderr)
        return 64

    control_modes = [
        bool(args.status),
        bool(args.cancel),
        bool(args.session_info),
        bool(args.end_session),
    ]
    if sum(control_modes) > 1:
        print(
            "ask-code: use only one of --status, --cancel, --session-info, "
            "or --end-session",
            file=sys.stderr,
        )
        return 64
    if args.status:
        return _check_status(url, api_key, args.status)
    if args.cancel:
        return _cancel_job(url, api_key, args.cancel)
    if args.session_info:
        return _session_info(url, api_key, args.session_info)
    if args.end_session:
        return _end_session(url, api_key, args.end_session)

    if not args.question:
        print(
            "ask-code: question is required unless using --status, --cancel, "
            "--session-info, or --end-session",
            file=sys.stderr,
        )
        return 64
    if not args.project:
        print(
            "ask-code: --project is required (or set ASK_CODE_PROJECT)",
            file=sys.stderr,
        )
        return 64
    if not args.caller or not args.caller.strip():
        print(
            "ask-code: --caller is required (or set ASK_CODE_CALLER); "
            "use 'unknown' only if you truly cannot determine the operator",
            file=sys.stderr,
        )
        return 64

    if args.session and args.follow_up:
        print(
            "ask-code: use only one of --session or --follow-up",
            file=sys.stderr,
        )
        return 64

    # Resolve which session (if any) this submit continues. --session is
    # strict and explicit; --follow-up reuses the saved session for this
    # (gateway, project) and quietly starts fresh when there is none.
    session_id_to_use = args.session
    session_source = "explicit" if args.session else None
    saved = _saved_session(url, args.project)
    continued_memo = None
    if args.follow_up:
        if saved:
            session_id_to_use = saved["session_id"]
            session_source = "follow-up"
            continued_memo = saved
        else:
            print(
                f"ask-code: no saved session for project {args.project!r} at "
                "this gateway; starting a new session",
                file=sys.stderr,
            )
    elif args.session and saved and saved.get("session_id") == args.session:
        # Explicit --session for the session we happen to remember: its memo
        # is still the best source for the frozen mode.
        continued_memo = saved

    mode = _resolve_mode(args.mode, continued_memo, args.exec)

    # Local prechecks for the exec tier — zero network, exit 64, exactly like
    # the --attach ones. They mirror request-shape rules the server also
    # enforces (exec needs debug; a budget needs exec); the numeric clamp range
    # is deliberately NOT mirrored, because the server clamps rather than
    # refuses and its bounds are operator-configured.
    if args.exec and mode != "debug":
        print(
            "ask-code: --exec requires --mode debug (exec is the reproduction "
            "tier of debug mode, not a separate disclosure profile)",
            file=sys.stderr,
        )
        return 64
    exec_tier = _resolve_exec(args.exec, mode, continued_memo)
    simulation_mode = _resolve_simulation_mode(
        args.simulation_mode, exec_tier, continued_memo
    )
    if args.simulation_mode is not None and not exec_tier:
        print(
            "ask-code: --simulation-mode requires --exec",
            file=sys.stderr,
        )
        return 64
    if exec_tier and simulation_mode is None:
        print(
            "ask-code: --simulation-mode is required with --exec",
            file=sys.stderr,
        )
        return 64
    if args.exec_budget_s is not None:
        if args.exec_budget_s <= 0:
            print(
                "ask-code: --exec-budget-s must be a positive number of seconds",
                file=sys.stderr,
            )
            return 64
        if not exec_tier:
            print("ask-code: --exec-budget-s requires --exec", file=sys.stderr)
            return 64

    try:
        attachments = _resolve_attachments(list(args.attachments or []))
    except _AttachmentError as exc:
        print(f"ask-code: {exc}", file=sys.stderr)
        return 64
    if attachments and mode != "debug":
        print(
            "ask-code: --attach requires --mode debug (attachments are debug "
            "inputs; the gateway rejects them on a qa request)",
            file=sys.stderr,
        )
        return 64

    body = _build_request_body(
        args,
        session_id=session_id_to_use,
        mode=mode,
        exec_tier=exec_tier,
        simulation_mode=simulation_mode,
    )
    if exec_tier and args.wait:
        # --wait cancels the job at the outer timeout (DELETE /job), and a case
        # reproduction routinely outlives the default 900s. Say so before the
        # submit rather than killing a half-finished run later.
        print(
            "ask-code: --wait cancels this exec turn at --timeout-s "
            f"({args.timeout_s}s), which a case run can easily exceed; prefer "
            "the async submit plus --status JOB_ID.",
            file=sys.stderr,
        )

    if args.dry_run:
        json.dump(body, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
        for attachment in attachments:
            print(
                f"ask-code: would attach {attachment['name']} "
                f"(kind={attachment['kind']}, {attachment['bytes']} bytes)",
                file=sys.stderr,
            )
        return 0

    try:
        status, payload = _submit_ask(url, api_key, body, attachments)
    except _AttachmentError as exc:
        print(f"ask-code: {exc}", file=sys.stderr)
        return 64

    # Auto-recovery is a --follow-up-only convenience: a saved session that
    # has expired / been closed / hit its turn cap (410) is dropped and the
    # question is resubmitted ONCE as a fresh ask. Explicit --session keeps
    # strict semantics (reported below, exit 5, no resubmit).
    if status == 410 and session_source == "follow-up":
        print(
            f"ask-code: session {session_id_to_use} expired/closed; starting "
            "a fresh session — earlier context is gone",
            file=sys.stderr,
        )
        # The saved session is known dead: forget it now, so even if the
        # fresh resubmit below fails (e.g. guard refusal) the next
        # --follow-up starts clean instead of re-recovering.
        _drop_saved_session_id(session_id_to_use)
        # Build a fresh body rather than mutating the one already sent. `mode`
        # stays: the new session simply freezes the same disclosure profile,
        # and the attachments are re-read from disk for the second upload.
        body = {k: v for k, v in body.items() if k != "session_id"}
        session_id_to_use = None
        session_source = None
        try:
            status, payload = _submit_ask(url, api_key, body, attachments)
        except _AttachmentError as exc:
            print(f"ask-code: {exc}", file=sys.stderr)
            return 64

    if status == 400 and isinstance(payload.get("detail"), dict) and payload["detail"].get("blocked"):
        reason = payload["detail"].get("reason", "input guard refused")
        print(f"REFUSED: {reason}", file=sys.stderr)
        return 2
    if status == 409 and session_id_to_use:
        print(
            f"ask-code: session {session_id_to_use} is busy — a previous turn "
            "is still in flight. Wait for that job to finish (poll it with "
            "--status JOB_ID from its submit output), then retry.",
            file=sys.stderr,
        )
        return 5
    if status == 410 and session_id_to_use:
        detail = payload.get("detail")
        detail_text = detail if isinstance(detail, str) else "expired or closed"
        print(
            f"ask-code: session {session_id_to_use} is unavailable "
            f"({detail_text}). Re-ask without --session to start a fresh session.",
            file=sys.stderr,
        )
        return 5
    if status >= 400:
        print(
            f"ask-code: server returned {status}: {payload.get('error') or payload.get('detail')}",
            file=sys.stderr,
        )
        return 3
    job_id = payload.get("job_id")
    if not job_id:
        print(f"ask-code: server did not return a job_id: {payload}", file=sys.stderr)
        return 3
    session_id = payload.get("session_id")
    session_expires_at = payload.get("session_expires_at")
    if session_id:
        # Every successful submit that returns a session refreshes the memo
        # (fresh asks and follow-ups alike). The mode and the exec tier ride
        # along so the next --follow-up on a debug/exec session repeats the
        # binding without the flags.
        _save_session(
            url,
            args.project,
            session_id,
            args.engine,
            session_expires_at,
            mode,
            exec_tier,
            simulation_mode,
        )

    if not args.wait:
        _print_submitted(job_id, session_id, session_expires_at)
        return 0

    return _wait_for_job(
        url,
        api_key,
        job_id,
        args.timeout_s,
        poll_after_s=payload.get("poll_after_s"),
    )


def _wait_for_job(
    url: str,
    api_key: str,
    job_id: str,
    timeout_s: int,
    poll_after_s: object = None,
) -> int:
    started = time.monotonic()
    # The server's own cadence hint wins over the historical 5s start: an exec
    # job answers in minutes to hours and 5s polling is pure noise.
    backoff = _poll_interval(poll_after_s, _DEFAULT_POLL_S)
    while True:
        if time.monotonic() - started > timeout_s:
            print("ask-code: outer timeout, cancelling job", file=sys.stderr)
            _http_json("DELETE", f"{url}/job/{job_id}", api_key)
            return 4

        time.sleep(backoff)
        backoff = min(backoff * 1.5, 30.0)

        status, payload = _http_json("GET", f"{url}/result/{job_id}", api_key)
        if status >= 400:
            print(
                f"ask-code: poll failed with {status}: {payload}", file=sys.stderr
            )
            return 3

        job_status = payload.get("status")
        if job_status in ("queued", "running"):
            # A job that keeps saying how long to wait (today: a running exec
            # turn) overrides the local backoff; everything else keeps it.
            backoff = _poll_interval(payload.get("poll_after_s"), backoff)
            continue
        return _handle_result_payload(job_id, payload)


def _check_status(url: str, api_key: str, job_id: str) -> int:
    status, payload = _http_json("GET", f"{url}/result/{job_id}", api_key)
    if status >= 400:
        print(f"ask-code: status failed with {status}: {payload}", file=sys.stderr)
        return 3
    return _handle_result_payload(job_id, payload)


def _cancel_job(url: str, api_key: str, job_id: str) -> int:
    status, payload = _http_json("DELETE", f"{url}/job/{job_id}", api_key)
    if status >= 400:
        print(f"ask-code: cancel failed with {status}: {payload}", file=sys.stderr)
        return 3
    print(f"Ask Code job {job_id} cancelled.")
    return 0


def _session_info(url: str, api_key: str, session_id: str) -> int:
    status, payload = _http_json("GET", f"{url}/session/{session_id}", api_key)
    if status >= 400:
        print(
            f"ask-code: session-info failed with {status}: {payload}",
            file=sys.stderr,
        )
        return 3
    _print_session_info(payload)
    return 0


def _end_session(url: str, api_key: str, session_id: str) -> int:
    status, payload = _http_json("DELETE", f"{url}/session/{session_id}", api_key)
    if status >= 400:
        print(
            f"ask-code: end-session failed with {status}: {payload}",
            file=sys.stderr,
        )
        return 3
    print(f"Ask Code session {session_id} ended (status: {payload.get('status', 'unknown')}).")
    # An explicitly ended session must not linger in the follow-up memo.
    _drop_saved_session_id(session_id)
    return 0


def _print_session_info(payload: dict) -> None:
    print(f"Session: {payload.get('session_id')}")
    print(f"  status: {payload.get('status')}")
    print(f"  project: {payload.get('project')}")
    components = payload.get("components") or []
    if components:
        print(f"  components: {', '.join(components)}")
    refs = payload.get("refs") or {}
    if refs:
        print(f"  refs: {refs}")
    print(f"  engine: {payload.get('engine')}")
    mode = payload.get("mode")
    if mode:
        # Servers older than debug mode omit the field entirely.
        print(f"  mode: {mode}")
    if payload.get("exec") is True:
        # Only printed for an exec session: it is part of the frozen binding a
        # follow-up must repeat, and silence means the analysis tier.
        print("  exec: true")
        simulation_mode = payload.get("simulation_mode")
        if simulation_mode:
            print(f"  simulation_mode: {simulation_mode}")
    print(f"  turns: {payload.get('turns')}")
    commits = payload.get("commits") or {}
    if commits:
        print("  commits:")
        for component, sha in sorted(commits.items()):
            print(f"    {component}: {sha}")
    expires_at = payload.get("expires_at")
    if expires_at:
        remaining = expires_at - time.time()
        if remaining > 0:
            print(f"  expires_at: {expires_at} (in {int(remaining // 60)} min)")
        else:
            print(f"  expires_at: {expires_at} (expired)")


def _handle_result_payload(job_id: str, payload: dict) -> int:
    job_status = payload.get("status")
    if job_status == "done":
        rc = _print_answer(payload)
    elif job_status == "blocked":
        reason = payload.get("blocked_reason") or "output guard blocked answer"
        print(f"REFUSED: {reason}", file=sys.stderr)
        rc = 2
    elif job_status == "cancelled":
        print("ask-code: job was cancelled", file=sys.stderr)
        rc = 4
    elif job_status == "error":
        reason = payload.get("error_reason") or "unknown error"
        print(f"ask-code: server error: {reason}", file=sys.stderr)
        rc = 3
    elif job_status in ("queued", "running"):
        print(f"Ask Code job {job_id} is still running ({job_status}).")
        print(_background_followup_hint(job_id))
        rc = 0
    else:
        print(f"ask-code: unexpected status {job_status!r}", file=sys.stderr)
        rc = 3
    # Additive: surface the follow-up hint whenever this result belongs to a
    # session, regardless of status — e.g. an errored/cancelled turn releases
    # its claim, so an immediate --session retry is valid.
    session_id = payload.get("session_id")
    if session_id:
        print(_session_followup_hint(session_id))
    return rc


def _remaining_minutes(expires_at: float) -> int:
    return int(max(0.0, expires_at - time.time()) // 60)


def _session_followup_hint(session_id: str, expires_at: float | None = None) -> str:
    if expires_at:
        return (
            f"Session: {session_id} (expires in {_remaining_minutes(expires_at)} min; "
            f"follow up with --follow-up or --session {session_id})"
        )
    # Without a known expiry (e.g. /result payloads) keep the original line.
    return (
        f"Session: {session_id} (follow up with --session {session_id}; "
        "idle timeout applies)"
    )


def _print_submitted(
    job_id: str,
    session_id: str | None = None,
    session_expires_at: float | None = None,
) -> None:
    print(f"Ask Code job submitted: {job_id}")
    print("This command returned immediately; do not synchronously wait in the current Hermes turn.")
    print(f"Check once: python ask_code.py --status {job_id}")
    print(_background_followup_hint(job_id))
    if session_id:
        print(_session_followup_hint(session_id, session_expires_at))


def _background_followup_hint(job_id: str) -> str:
    return (
        "Hermes follow-up: keep the job id and run `python ask_code.py --status "
        f"{job_id}` in a later turn or a bounded background terminal until the "
        "job finishes, then report the result."
    )


def _print_answer(payload: dict) -> int:
    answer = payload.get("answer") or ""
    commits = payload.get("commits") or {}

    print(answer.rstrip())
    if commits:
        print("\n---")
        print("Resolved commits:")
        for component, sha in commits.items():
            print(f"- {component}: {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
