from __future__ import annotations

import argparse
import math
from typing import Any

from _teap_agent import TeapCommandError, emit, handle_command_error, run_teap


MAX_FINDINGS = 256
TS_PAGE_SIZE = 1000


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a TEAP case before task submission.")
    parser.add_argument("case_path")
    args = parser.parse_args()
    try:
        result = validate_case(args.case_path)
    except TeapCommandError as exc:
        handle_command_error(exc, context="case submission validation")
    emit(result, exit_code=0 if result["valid"] else 2)


def validate_case(case_path: str) -> dict[str, Any]:
    scenario_selected = run_teap(
        [
            "-o",
            "json",
            "case",
            "parameter",
            "get",
            case_path,
            "--path",
            "case_info.scenario_selected",
        ]
    ).get("value")
    schema = run_teap(["-o", "json", "case", "schema"])
    required_by_sheet = {
        str(item["sheet"]): [str(field) for field in item.get("required_fields", [])]
        for item in schema.get("sheets", [])
        if isinstance(item, dict) and item.get("sheet") not in {"parameter", "meta_config"}
    }
    has_in_service_by_sheet = {
        str(item["sheet"]): bool(item.get("has_in_service"))
        for item in schema.get("sheets", [])
        if isinstance(item, dict) and item.get("sheet")
    }
    # Any sheet (load/gen/wind/solar/feedin/... or the huge timeseries) can
    # exceed the 2 MiB script output cap on big cases, so read every sheet
    # through bounded server-side --index-range pages. No .tc file is touched.
    sheet_payloads = {
        sheet: {"rows": _fetch_sheet_paginated(case_path, sheet)}
        for sheet in sorted(required_by_sheet)
    }
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for sheet, payload in sheet_payloads.items():
        if sheet == "timeseries":
            continue
        required = required_by_sheet.get(sheet, [])
        for row in payload.get("rows", []):
            if not isinstance(row, dict):
                continue
            if has_in_service_by_sheet.get(sheet, False) and row.get("in_service") is not True:
                continue
            missing = [field for field in required if _missing(row.get(field))]
            if missing:
                issues.append(_issue("required_device_field_missing", sheet, row, fields=missing))

    _validate_storage_bindings(sheet_payloads, issues)
    timeseries_rows = sheet_payloads.get("timeseries", {}).get("rows", [])
    inventory = run_teap(["-o", "json", "timeseries", "types", case_path])
    _validate_timeseries(timeseries_rows, inventory, issues, warnings)
    _validate_scenario(scenario_selected, timeseries_rows, warnings)
    issue_count = len(issues)
    warning_count = len(warnings)
    return {
        "ok": True,
        "case_path": case_path,
        "valid": not issues,
        "issue_count": issue_count,
        "warning_count": warning_count,
        "issues": issues[:MAX_FINDINGS],
        "warnings": warnings[:MAX_FINDINGS],
        "issues_truncated": issue_count > MAX_FINDINGS,
        "warnings_truncated": warning_count > MAX_FINDINGS,
    }


def _fetch_sheet_paginated(case_path: str, sheet: str, page_size: int = TS_PAGE_SIZE) -> list[dict[str, Any]]:
    """Read one whole sheet through bounded server-side range pages.

    Every request stays under the script's response-output cap for large cases
    (and page sizes are capped locally so no single page can exceed it). All
    reads run on the TEAP server; no .tc file is downloaded or parsed.
    """
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        payload = run_teap(
            [
                "-o",
                "json",
                "case",
                "sheet",
                "get",
                case_path,
                sheet,
                "--index-range",
                f"{start}-{start + page_size - 1}",
            ]
        )
        page_rows = payload.get("rows", [])
        if not isinstance(page_rows, list):
            raise TeapCommandError(
                "sheet_page_invalid",
                False,
                (
                    f"sheet [{sheet}] page [{start}-{start + page_size - 1}] "
                    "returned an invalid shape; correct the CLI or report it.",
                ),
                2,
            )
        rows.extend(item for item in page_rows if isinstance(item, dict))
        max_index = payload.get("max_index")
        if not isinstance(max_index, int):
            raise TeapCommandError(
                "sheet_page_invalid",
                False,
                (
                    f"sheet [{sheet}] page [{start}-{start + page_size - 1}] "
                    "did not return an integer max_index; correct the CLI or report it.",
                ),
                2,
            )
        if max_index <= start + page_size - 1:
            break
        start += page_size
        if start > 10_000_000:
            raise TeapCommandError(
                "sheet_paging_unbounded",
                False,
                (f"sheet [{sheet}] paging exceeded its safety bound; report this finding.",),
                2,
            )
    return rows


def _validate_storage_bindings(payloads: dict[str, dict[str, Any]], issues: list[dict[str, Any]]) -> None:
    for stogen_sheet, storage_sheet in (("stogen", "storage"), ("stogen_plan", "storage_plan")):
        storage_ids = {
            row.get("index") for row in payloads.get(storage_sheet, {}).get("rows", []) if isinstance(row, dict)
        }
        for row in payloads.get(stogen_sheet, {}).get("rows", []):
            if not isinstance(row, dict) or row.get("in_service") is not True:
                continue
            target = row.get("storage")
            if _missing(target):
                issues.append(_issue("stogen_storage_binding_missing", stogen_sheet, row, fields=["storage"]))
            elif target not in storage_ids:
                issues.append(_issue("stogen_storage_target_missing", stogen_sheet, row, fields=["storage"], target=target))


def _validate_timeseries(rows: Any, inventory: dict[str, Any], issues: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> None:
    allowed: dict[str, str] = {}
    for sheet in inventory.get("sheets", []):
        if not isinstance(sheet, dict):
            continue
        for item in sheet.get("types", []):
            if isinstance(item, dict) and isinstance(item.get("value"), str):
                allowed[item["value"]] = str(item.get("recommended_value_type") or ("multiply" if item["value"].endswith(".p_rate") else "replace"))
    value_types = set(inventory.get("value_types") or ["replace", "multiply"])
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        ts_type = row.get("type")
        if "value_type" in row and "data_type" in row and row["value_type"] != row["data_type"]:
            issues.append(
                _issue(
                    "conflicting_timeseries_value_type",
                    "timeseries",
                    row,
                    fields=["value_type", "data_type"],
                )
            )
            continue
        value_type = row.get("value_type", row.get("data_type"))
        if ts_type not in allowed:
            issues.append(_issue("invalid_timeseries_type", "timeseries", row, fields=["type"], value=ts_type))
        if value_type not in value_types:
            issues.append(_issue("invalid_timeseries_value_type", "timeseries", row, fields=["value_type"], value=value_type))
        elif ts_type in allowed and value_type != allowed[ts_type]:
            warnings.append(_issue("timeseries_value_type_not_recommended", "timeseries", row, value_type=value_type, recommended_value_type=allowed[ts_type]))


def _validate_scenario(selected: Any, rows: Any, warnings: list[dict[str, Any]]) -> None:
    if _missing(selected):
        warnings.append({"code": "scenario_not_selected", "hints": ["Select an explicit calculation scenario before task submission."]})
        return
    matching = [row for row in rows if isinstance(row, dict) and row.get("scenario") in (None, "", selected)]
    if not matching:
        warnings.append({
            "code": "selected_scenario_without_timeseries",
            "scenario": selected,
            "hints": ["Add or bind at least one generic or selected-scenario timeseries, or verify that a static calculation is intended before submission."],
        })


def _issue(code: str, sheet: str, row: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "code": code,
        "sheet": sheet,
        "row_id": row.get("index"),
        "name": row.get("name"),
        **extra,
        "retryable": False,
        "hints": ["Correct the identified case object, then rerun this validation; do not submit while this finding remains."],
    }


def _missing(value: Any) -> bool:
    return (
        value is None
        or (isinstance(value, str) and not value.strip())
        or (isinstance(value, float) and math.isnan(value))
    )


if __name__ == "__main__":
    main()
