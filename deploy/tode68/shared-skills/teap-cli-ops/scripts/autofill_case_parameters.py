from __future__ import annotations

import argparse

from _teap_agent import TeapCommandError, emit, emit_error, handle_command_error, run_teap


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill all supported case device parameters through TEAP Core.")
    parser.add_argument("case_path")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if not args.confirm:
        emit_error(
            "confirmation_required",
            hints=(
                f"Read and validate [{args.case_path}], then rerun with `--confirm` only when whole-case mutation is authorized.",
            ),
        )
    try:
        payload = run_teap(["-o", "json", "case", "autofill", args.case_path, "--confirm"])
    except TeapCommandError as exc:
        handle_command_error(exc, context="case autofill")
    emit(payload)


if __name__ == "__main__":
    main()
