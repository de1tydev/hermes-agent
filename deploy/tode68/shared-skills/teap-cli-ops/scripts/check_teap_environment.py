from __future__ import annotations

import argparse

from _teap_agent import TeapCommandError, emit, emit_error, handle_command_error, run_teap, sanitize_base_url, teap_version


def main() -> None:
    parser = argparse.ArgumentParser(description="Check whether teap-cli and its configured TEAP environment are ready.")
    parser.parse_args()
    try:
        version = teap_version()
        auth = run_teap(["-o", "json", "auth", "status"])
    except TeapCommandError as exc:
        handle_command_error(exc, context="environment check")

    service = {
        "reachable": True,
        "base_url": sanitize_base_url(auth.get("base_url")),
        "auth_required": bool(auth.get("auth_required")),
    }
    if not auth.get("auth_required"):
        emit({"ok": True, "status": "ready", "version": version, "service": service})

    service.update(
        {
            "authenticated": bool(auth.get("auth_token_valid")),
            "refresh_available": bool(auth.get("refresh_token_present")),
        }
    )
    if auth.get("auth_token_valid") and not auth.get("auth_token_expired"):
        emit({"ok": True, "status": "ready", "version": version, "service": service})

    emit_error(
        "authentication_refresh_required",
        hints=(
            "Token authentication is expired or invalid; refresh the host page or session so it can provide renewed credentials.",
            "Do not paste a token into agent output and do not repeat this check until credentials change.",
        ),
        extra={"status": "not_ready", "version": version, "service": service},
    )


if __name__ == "__main__":
    main()
