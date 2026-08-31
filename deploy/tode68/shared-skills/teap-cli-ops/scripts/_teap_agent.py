from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Sequence
from urllib.parse import urlsplit, urlunsplit


MAX_OUTPUT_BYTES = 2 * 1024 * 1024
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_TOKEN_FIELD_PATTERN = re.compile(
    r"(?i)((?:[\"']?)\b(?:access|auth|refresh|id)?_?token\b(?:[\"']?)"
    r"\s*[:=]\s*(?:[\"']?))([^\"'\s,;&}]+)"
)
_TOKEN_QUERY_PATTERN = re.compile(
    r"(?i)([?&](?:access|auth|refresh|id)?_?token=)([^&#\s]+)"
)


@dataclass(frozen=True)
class TeapCommandError(Exception):
    code: str
    retryable: bool
    hints: tuple[str, ...]
    exit_code: int = 1
    message: str | None = None
    api_code: int | str | None = None
    http_status: int | None = None


_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)^(?:token|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|authorization|client[_-]?secret|api[_-]?key|password|secret)$"
)


def run_teap(args: Sequence[str], *, timeout: float = 120) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["teap", *args],
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise TeapCommandError(
            "teap_cli_unavailable",
            False,
            ("Install teap-cli and ensure `teap` is available on PATH; repeating this script will not succeed until then.",),
            2,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise TeapCommandError(
            "service_timeout",
            True,
            ("Verify service connectivity, then retry once; stop if the timeout repeats.",),
            3,
        ) from exc

    stdout = completed.stdout[:MAX_OUTPUT_BYTES]
    stderr = completed.stderr[:MAX_OUTPUT_BYTES]
    payload = _parse_json(stdout) or _parse_json(stderr)
    if completed.returncode != 0:
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        raise TeapCommandError(
            str(error.get("code") or "teap_command_failed"),
            bool(error.get("retryable", False)),
            tuple(sanitize_text(item) for item in error.get("hints", []) if item),
            completed.returncode,
            sanitize_text(error.get("message")) if error.get("message") else None,
            error.get("api_code"),
            error.get("http_status"),
        )
    if not isinstance(payload, dict):
        raise TeapCommandError(
            "invalid_teap_output",
            False,
            ("Run the same `teap -o json` command directly and report the invalid JSON contract; do not retry this script unchanged.",),
            5,
        )
    return payload


def teap_version() -> str:
    try:
        completed = subprocess.run(
            ["teap", "--version"],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except FileNotFoundError as exc:
        raise TeapCommandError(
            "teap_cli_unavailable",
            False,
            ("Install teap-cli and ensure `teap` is available on PATH; repeating this script will not succeed until then.",),
            2,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise TeapCommandError("teap_cli_timeout", False, ("Repair the local teap-cli installation before retrying.",), 2) from exc
    if completed.returncode != 0:
        raise TeapCommandError("teap_cli_unavailable", False, ("Repair the local teap-cli installation before retrying.",), 2)
    text = completed.stdout.strip()
    parsed = _parse_json(text)
    if isinstance(parsed, dict) and parsed.get("version"):
        return str(parsed["version"])
    match = re.search(r"\b(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?)\b", text)
    if not match:
        raise TeapCommandError("invalid_teap_version", False, ("Run `teap --version` and repair the installation if no semantic version is returned.",), 2)
    return match.group(1)


def emit(payload: dict[str, Any], *, exit_code: int = 0) -> None:
    print(json.dumps(_sanitize_payload(payload), ensure_ascii=False, separators=(",", ":")))
    raise SystemExit(exit_code)


def emit_error(
    code: str,
    *,
    hints: Sequence[str],
    retryable: bool = False,
    exit_code: int = 2,
    message: str | None = None,
    api_code: int | str | None = None,
    http_status: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "ok": False,
        "error": {
            "code": code,
            "message": sanitize_text(message or code),
            "retryable": retryable,
            "hints": [sanitize_text(hint) for hint in hints],
            "api_code": api_code,
            "http_status": http_status,
        },
    }
    if extra:
        payload.update(extra)
    emit(payload, exit_code=exit_code)


def handle_command_error(exc: TeapCommandError, *, context: str) -> None:
    code = "service_unreachable" if exc.code in {"service_unreachable", "service_timeout"} else exc.code
    hints = exc.hints or (f"Run the underlying {context} command directly, correct the reported condition, and stop if it is non-retryable.",)
    emit_error(
        code,
        message=exc.message or f"The underlying {context} command failed.",
        hints=hints,
        retryable=exc.retryable,
        exit_code=exc.exit_code,
        api_code=exc.api_code,
        http_status=exc.http_status,
    )


def _parse_json(raw: str) -> Any:
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def sanitize_text(value: object) -> str:
    text = str(value)
    text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    text = _TOKEN_FIELD_PATTERN.sub(r"\1[REDACTED]", text)
    return _TOKEN_QUERY_PATTERN.sub(r"\1[REDACTED]", text)


def sanitize_base_url(value: object) -> str:
    try:
        parsed = urlsplit(str(value))
    except ValueError:
        return "[REDACTED]"
    if not parsed.scheme or not parsed.hostname:
        return "[REDACTED]"
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    netloc = f"{host}:{parsed.port}" if parsed.port is not None else host
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _sanitize_payload(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {str(item_key): _sanitize_payload(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_payload(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_payload(item, key=key) for item in value]
    if isinstance(value, str):
        if key and _SENSITIVE_KEY_PATTERN.fullmatch(key):
            return "[REDACTED]"
        return sanitize_base_url(value) if key == "base_url" else sanitize_text(value)
    return value
