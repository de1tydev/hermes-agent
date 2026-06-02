#!/usr/bin/env python3
"""Validate the S0 capability manifest.

This validator is intentionally narrow: S0 is an evidence-freeze stage, so the
manifest must be complete enough for downstream stages to fail closed when a
source root, command, config hash, or LLM-helper decision is missing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


REQUIRED_EVIDENCE_FILES = {
    "evidence/S0-openclaw-capability.md",
    "evidence/S0-hermes-capability.md",
    "evidence/S0-current-config.md",
    "evidence/S0-source-test-command-matrix.md",
}


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level value must be an object")
    return data


def _get(data: dict[str, Any], dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise ValueError(f"missing required field: {dotted}")
        cur = cur[part]
    return cur


def _require_non_empty_string(data: dict[str, Any], dotted: str) -> str:
    value = _get(data, dotted)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{dotted} must be a non-empty string")
    return value


def _require_bool(data: dict[str, Any], dotted: str) -> bool:
    value = _get(data, dotted)
    if not isinstance(value, bool):
        raise ValueError(f"{dotted} must be a boolean")
    return value


def _require_existing_path(path_text: str, *, expect_dir: bool | None = None) -> None:
    path = Path(path_text)
    if not path.exists():
        raise ValueError(f"path does not exist: {path_text}")
    if expect_dir is True and not path.is_dir():
        raise ValueError(f"path must be a directory: {path_text}")
    if expect_dir is False and not path.is_file():
        raise ValueError(f"path must be a file: {path_text}")


def _validate(data: dict[str, Any]) -> None:
    stage_id = _require_non_empty_string(data, "stage_id")
    if stage_id != "S0-evidence-boundary":
        raise ValueError(f"stage_id must be S0-evidence-boundary, got {stage_id!r}")

    evidence_files = _get(data, "evidence_files")
    if not isinstance(evidence_files, list):
        raise ValueError("evidence_files must be a list")
    missing = REQUIRED_EVIDENCE_FILES.difference(evidence_files)
    if missing:
        raise ValueError(f"evidence_files missing required entries: {sorted(missing)}")
    for evidence_file in REQUIRED_EVIDENCE_FILES:
        _require_existing_path(evidence_file, expect_dir=False)

    for dotted in (
        "repo_roots.hermes.path",
        "repo_roots.hermes.branch",
        "repo_roots.hermes.baseline_commit",
        "repo_roots.openclaw_hindsight.path",
        "repo_roots.openclaw_hindsight.branch",
        "repo_roots.openclaw_hindsight.commit",
        "openclaw.runtime.package_cwd",
        "openclaw.runtime.runtime_dist",
        "openclaw.source_package.package_cwd",
        "openclaw.source_package.package_json",
        "hermes.provider_lifecycle.provider_class",
        "hermes.config_schema.provider_schema_path",
        "config_snapshot.raw_evidence_root",
    ):
        _require_non_empty_string(data, dotted)

    _require_existing_path(_get(data, "repo_roots.hermes.path"), expect_dir=True)
    _require_existing_path(_get(data, "repo_roots.openclaw_hindsight.path"), expect_dir=True)
    _require_existing_path(_get(data, "openclaw.runtime.package_cwd"), expect_dir=True)
    _require_existing_path(_get(data, "openclaw.runtime.runtime_dist"), expect_dir=False)
    _require_existing_path(_get(data, "openclaw.source_package.package_cwd"), expect_dir=True)
    _require_existing_path(_get(data, "openclaw.source_package.package_json"), expect_dir=False)
    _require_existing_path(_get(data, "config_snapshot.raw_evidence_root"), expect_dir=True)

    for dotted in (
        "openclaw.commands.build",
        "openclaw.commands.unit_test",
        "openclaw.commands.integration_test",
        "hermes.commands.full_tests",
        "hermes.commands.s0_validator",
    ):
        _require_non_empty_string(data, dotted)

    hashes = _get(data, "config_snapshot.redacted_hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("config_snapshot.redacted_hashes must be a non-empty object")
    for name, value in hashes.items():
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError(f"redacted hash {name} must be a 64-character SHA-256 hex string")
        int(value, 16)

    llm_available = _require_bool(data, "openclaw.llm_helper.available")
    _require_non_empty_string(data, "openclaw.llm_helper.decision")
    _require_non_empty_string(data, "openclaw.llm_helper.evidence")
    if not llm_available:
        _require_non_empty_string(data, "openclaw.llm_helper.skip_or_fail_reason")

    _require_bool(data, "downstream_guards.node_modules_dist_is_not_implementation_target")
    _require_bool(data, "downstream_guards.missing_openclaw_llm_helper_requires_noop_or_correct_course")
    raw_secrets = _require_bool(data, "downstream_guards.raw_secret_values_committed")
    if raw_secrets:
        raise ValueError("downstream_guards.raw_secret_values_committed must be false")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: validate-s0-capability-manifest.py <manifest.json>", file=sys.stderr)
        return 2
    try:
        _validate(_load(Path(argv[1])))
    except Exception as exc:
        print(f"S0 capability manifest validation failed: {exc}", file=sys.stderr)
        return 1
    print("S0 capability manifest validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
