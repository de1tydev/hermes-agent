#!/usr/bin/env python3
"""Collect and normalize AI usage insight inputs.

This script intentionally uses only the Python standard library.  It calls the
existing daily-usage-rankings endpoint serially, normalizes the response into an
allowlisted schema, and avoids emitting fake JSON on fatal errors.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterable


SCHEMA_VERSION = "ai_usage_insights.v1"
DEFAULT_ENDPOINT_URL = "https://aistat.tode.ltd/analytics/api/daily-usage-rankings"
DEFAULT_ENDPOINT_ENV_VAR = "AI_USAGE_ENDPOINT_URL"
DEFAULT_TOKEN_ENV_VAR = "AI_USAGE_API_TOKEN"
SHANGHAI_TZ = dt.timezone(dt.timedelta(hours=8))
UNAVAILABLE = "unavailable"
SCOPE_RANKED_ROWS_ONLY = "ranked_rows_only"
SCOPE_LIMIT_BOUNDARY_UNKNOWN = "limit_boundary_unknown"
SCOPE_WITHIN_ENDPOINT_LIMIT = "within_endpoint_limit"
RETRYABLE_HTTP_STATUS = {408, 429, 502, 503, 504}
NUMERIC_FIELDS = (
    "requests",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "cost",
)
TOKEN_COMPONENT_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
SENSITIVE_KEY_PATTERN = re.compile(
    r"^(authorization|cookie|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|"
    r"token|key[_-]?(id|ids|name|names)|source[_-]?field|source[_-]?fields)$",
    re.IGNORECASE,
)
THINKING_SOURCE = "newapi.logs.other.reasoning_effort"
THINKING_SCOPE = "newapi_only_current_period"
THINKING_COVERAGE_FIELDS = (
    "requests_total",
    "requests_recorded",
    "requests_unrecorded",
    "recorded_ratio",
)
THINKING_LEVEL_FIELDS = (
    "level",
    "requests",
    "request_share",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "total_tokens",
    "cost",
    "avg_input_tokens",
    "avg_output_tokens",
    "avg_duration_ms",
    "lightweight_candidate_requests",
    "lightweight_candidate_ratio",
)
THINKING_USER_FIELDS = (
    "user",
    "model",
    "requests_total",
    "requests_recorded",
    "requests_unrecorded",
    "recorded_ratio",
    "levels",
)


class FatalError(Exception):
    pass


@dataclass
class FetchResult:
    ok: bool
    data: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    deadline_skipped: bool = False


def parse_iso_datetime(value: str) -> dt.datetime:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise FatalError("--end-at must include time and timezone offset")
    normalized = value
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise FatalError(f"invalid --end-at: {exc}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FatalError("--end-at must include timezone offset")
    return parsed.astimezone(SHANGHAI_TZ)


def isoformat_seconds(value: dt.datetime) -> str:
    return value.astimezone(SHANGHAI_TZ).replace(microsecond=0).isoformat()


def generate_history_windows(end_at: dt.datetime, workdays: int) -> list[dt.datetime]:
    windows: list[dt.datetime] = []
    candidate_date = end_at.astimezone(SHANGHAI_TZ).date() - dt.timedelta(days=1)
    trigger_time = end_at.astimezone(SHANGHAI_TZ).timetz().replace(tzinfo=None)
    while len(windows) < workdays:
        if candidate_date.weekday() < 5:
            windows.append(dt.datetime.combine(candidate_date, trigger_time, SHANGHAI_TZ))
        candidate_date -= dt.timedelta(days=1)
    return windows


def window_start(end_at: dt.datetime, hours: float) -> dt.datetime:
    return end_at - dt.timedelta(hours=hours)


def positive_int_range(name: str, value: int, minimum: int, maximum: int) -> int:
    if value < minimum or value > maximum:
        raise FatalError(f"{name} must be in range {minimum}..{maximum}")
    return value


def positive_float(name: str, value: float) -> float:
    if value <= 0:
        raise FatalError(f"{name} must be positive")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect AI usage insight JSON")
    parser.add_argument("--endpoint-url", default=None)
    parser.add_argument("--endpoint-env-var", default=DEFAULT_ENDPOINT_ENV_VAR)
    parser.add_argument("--token-env-var", default=DEFAULT_TOKEN_ENV_VAR)
    parser.add_argument("--end-at", required=True)
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--baseline-workdays", type=int, default=10)
    parser.add_argument("--trend-workdays", type=int, default=10)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--deadline", type=float, default=180.0)
    parser.add_argument("--minimum-request-seconds", type=float, default=3.0)
    parser.add_argument("--debug-full-output", "--debug", dest="debug_full_output", default=None)
    return parser


def resolve_endpoint(args: argparse.Namespace, environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    endpoint = args.endpoint_url or env.get(args.endpoint_env_var) or DEFAULT_ENDPOINT_URL
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise FatalError("--endpoint-url must be a complete http(s) URL")
    return endpoint


def redact_text(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme and parsed.netloc:
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        redacted_query = []
        for key, item_value in query:
            redacted_query.append((key, "REDACTED" if SENSITIVE_KEY_PATTERN.search(key) else item_value))
        value = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(redacted_query), parsed.fragment)
        )
    value = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer REDACTED", value, flags=re.IGNORECASE)
    value = re.sub(r"([?&](?:token|api[_-]?key|secret|access[_-]?token)=)[^&\s]+", r"\1REDACTED", value, flags=re.I)
    return value


def endpoint_with_query(endpoint: str, params: dict[str, str]) -> str:
    parsed = urllib.parse.urlsplit(endpoint)
    existing = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = urllib.parse.urlencode(existing + sorted(params.items()))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def request_json(url: str, timeout: float, token: str | None) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise FatalError(f"invalid JSON response from endpoint: {exc}") from exc
    if not isinstance(data, dict):
        raise FatalError("endpoint JSON response must be an object")
    return data


def fetch_with_retries(
    endpoint: str,
    params: dict[str, str],
    timeout: float,
    retries: int,
    deadline_at: float,
    minimum_request_seconds: float,
    token: str | None,
    allow_deadline_skip: bool,
    requester: Callable[[str, float, str | None], dict[str, Any]] = request_json,
) -> FetchResult:
    url = endpoint_with_query(endpoint, params)
    attempts = max(0, retries) + 1
    last_error: dict[str, Any] | None = None
    for attempt in range(attempts):
        remaining = deadline_at - time.monotonic()
        if remaining < minimum_request_seconds:
            return FetchResult(
                ok=False,
                deadline_skipped=True,
                error={"type": "deadline_skipped", "message": "remaining deadline below minimum_request_seconds"},
            )
        effective_timeout = min(timeout, remaining)
        try:
            return FetchResult(ok=True, data=requester(url, effective_timeout, token))
        except urllib.error.HTTPError as exc:
            retryable = exc.code in RETRYABLE_HTTP_STATUS
            last_error = {
                "type": "http_error",
                "status": exc.code,
                "message": redact_text(str(exc.reason)),
                "retryable": retryable,
            }
            if not retryable:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = {"type": "network_error", "message": redact_text(str(exc)), "retryable": True}
        except FatalError as exc:
            last_error = {"type": "response_error", "message": redact_text(str(exc)), "retryable": False}
            break
        if attempt < attempts - 1:
            remaining = deadline_at - time.monotonic()
            if remaining < minimum_request_seconds:
                return FetchResult(
                    ok=False,
                    deadline_skipped=True,
                    error={"type": "deadline_skipped", "message": "remaining deadline below retry budget"},
                )
            sleep_seconds = min(2**attempt * 0.25, 2.0, max(0.0, remaining - minimum_request_seconds))
            if sleep_seconds:
                time.sleep(sleep_seconds)
    return FetchResult(ok=False, error=last_error or {"type": "unknown_error", "message": "request failed"})


def first_present(row: dict[str, Any], keys: Iterable[str]) -> tuple[str | None, Any]:
    for key in keys:
        if key in row:
            return key, row[key]
    return None, None


def parse_numeric(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if isinstance(value, str):
        stripped = value.strip().replace(",", "")
        if not stripped:
            return None
        try:
            return float(stripped) if "." in stripped else int(stripped)
        except ValueError:
            return None
    return None


def fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def normalize_entity_key(entity_type: str, row: dict[str, Any], display_name: str) -> tuple[str, str]:
    stable_key, stable_value = first_present(row, ("stable_user_id", "stable_model_id"))
    if stable_key and stable_value not in (None, ""):
        return f"{entity_type}_{fingerprint(stable_value)}", "stable_id"
    key_ids_key, key_ids_value = first_present(row, ("key_ids", "key_id"))
    if key_ids_key and key_ids_value:
        return f"{entity_type}_{fingerprint(sorted_list(key_ids_value))}", "id_fingerprint"
    key_names_key, key_names_value = first_present(row, ("key_names", "key_name"))
    if key_names_key and key_names_value:
        return f"{entity_type}_{fingerprint(sorted_list(key_names_value))}", "name_fingerprint"
    return f"{entity_type}_{fingerprint(display_name)}", "display_name_fingerprint"


def sorted_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return sorted(str(item) for item in value)
    return [str(value)]


def normalize_row(row: dict[str, Any], entity_type: str, rank_index: int) -> dict[str, Any]:
    if entity_type == "user":
        _, display = first_present(row, ("user", "user_name", "name"))
    else:
        _, display = first_present(row, ("model", "model_name", "name"))
    display_name = str(display) if display not in (None, "") else "unknown"
    entity_key, key_source = normalize_entity_key(entity_type, row, display_name)
    field_map = {
        "requests": ("requests", "request_count", "请求数"),
        "input_tokens": ("input_tokens", "prompt_tokens", "input"),
        "output_tokens": ("output_tokens", "completion_tokens", "output"),
        "cache_read_tokens": ("cache_read_tokens", "cache_read"),
        "cache_creation_tokens": ("cache_creation_tokens", "cache_creation"),
        "total_tokens": ("total_tokens", "total"),
        "cost": ("cost", "total_cost", "费用"),
    }
    numeric: dict[str, Any] = {}
    missing_fields: list[str] = []
    null_fields: list[str] = []
    for normalized, aliases in field_map.items():
        source_key, value = first_present(row, aliases)
        if source_key is None:
            missing_fields.append(normalized)
            numeric[normalized] = UNAVAILABLE
            continue
        parsed = parse_numeric(value)
        if parsed is None:
            null_fields.append(normalized)
            numeric[normalized] = UNAVAILABLE
        else:
            numeric[normalized] = parsed
    if numeric["total_tokens"] == UNAVAILABLE:
        component_values = [numeric[key] for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")]
        derived_fields = []
        if all(value != UNAVAILABLE for value in component_values):
            numeric["total_tokens"] = sum(component_values)
            derived_fields.append("total_tokens")
            missing_fields = [item for item in missing_fields if item != "total_tokens"]
            null_fields = [item for item in null_fields if item != "total_tokens"]
    else:
        derived_fields = []
    output = {
        "entity_type": entity_type,
        "entity_key": entity_key,
        "entity_key_source": key_source,
        "display_name": display_name,
        "rank": parse_numeric(row.get("rank")) or rank_index,
    }
    output.update(numeric)
    output["data_quality"] = {"missing_fields": missing_fields, "null_fields": null_fields, "derived_fields": derived_fields}
    return output


def extract_section(response: dict[str, Any], section: str = "current") -> dict[str, Any]:
    if section in response and isinstance(response[section], dict):
        return response[section]
    return response


def normalize_response(response: dict[str, Any], section: str = "current") -> dict[str, Any]:
    data = extract_section(response, section)
    users_raw = data.get("users") if isinstance(data.get("users"), list) else []
    models_raw = data.get("models") if isinstance(data.get("models"), list) else []
    return {
        "period": data.get("period") if isinstance(data.get("period"), dict) else period_from_top_level(data),
        "users": [normalize_row(row, "user", index + 1) for index, row in enumerate(users_raw) if isinstance(row, dict)],
        "models": [normalize_row(row, "model", index + 1) for index, row in enumerate(models_raw) if isinstance(row, dict)],
    }


def period_from_top_level(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "start_at": data.get("start_at") or data.get("start"),
        "end_at": data.get("end_at") or data.get("end"),
        "duration_hours": data.get("duration_hours"),
        "range_label": data.get("range_label"),
    }


def scope_for_length(row_count: int, limit: int) -> str:
    if row_count >= limit:
        return SCOPE_LIMIT_BOUNDARY_UNKNOWN
    return SCOPE_RANKED_ROWS_ONLY


def weakest_scope(scopes: Iterable[str]) -> str:
    order = {SCOPE_WITHIN_ENDPOINT_LIMIT: 0, SCOPE_RANKED_ROWS_ONLY: 1, SCOPE_LIMIT_BOUNDARY_UNKNOWN: 2}
    return max(scopes, key=lambda scope: order.get(scope, 3))


def confidence_for_scope(scope: str) -> str:
    if scope == SCOPE_WITHIN_ENDPOINT_LIMIT:
        return "high"
    if scope == SCOPE_RANKED_ROWS_ONLY:
        return "low"
    return "low"


def metric_meta(scope: str, confidence: str | None = None) -> dict[str, str]:
    return {"scope": scope, "confidence": confidence or confidence_for_scope(scope)}


def numeric_or_none(value: Any) -> float | None:
    if value == UNAVAILABLE:
        return None
    parsed = parse_numeric(value)
    return float(parsed) if parsed is not None else None


def safe_sum(rows: list[dict[str, Any]], field: str) -> int | float | str:
    total = 0.0
    any_value = False
    for row in rows:
        value = numeric_or_none(row.get(field))
        if value is None:
            return UNAVAILABLE
        any_value = True
        total += value
    if not any_value:
        return 0
    return int(total) if total.is_integer() else total


def safe_divide(numerator: Any, denominator: Any) -> float | None:
    left = numeric_or_none(numerator)
    right = numeric_or_none(denominator)
    if left is None or right is None or right == 0:
        return None
    return left / right


def is_available(value: Any) -> bool:
    return numeric_or_none(value) is not None


def percent_change(current: Any, reference: Any) -> float | str:
    ratio = safe_divide(current, reference)
    if ratio is None:
        return UNAVAILABLE
    return (ratio - 1.0) * 100.0


def maybe_number(value: float | str) -> int | float | str:
    if value == UNAVAILABLE:
        return UNAVAILABLE
    if not math.isfinite(value):
        return UNAVAILABLE
    return int(value) if value.is_integer() else value


def aggregate_rows(rows: list[dict[str, Any]], source: str, scope: str) -> dict[str, Any]:
    totals = {
        "source": source,
        "scope": scope,
        "confidence": confidence_for_scope(scope),
    }
    for field in NUMERIC_FIELDS:
        totals[field] = safe_sum(rows, field)
    tokens = totals["total_tokens"]
    requests = totals["requests"]
    cost = totals["cost"]
    totals["tokens_per_request"] = safe_divide(tokens, requests)
    totals["cost_per_request"] = safe_divide(cost, requests)
    cost_per_million = safe_divide(cost, tokens)
    totals["cost_per_million_tokens"] = cost_per_million * 1_000_000 if cost_per_million is not None else None
    return totals


def model_total_diff_ratio(user_totals: dict[str, Any], model_totals: dict[str, Any]) -> float | str:
    user_tokens = numeric_or_none(user_totals.get("total_tokens"))
    model_tokens = numeric_or_none(model_totals.get("total_tokens"))
    if user_tokens is None or model_tokens is None or user_tokens == 0:
        return UNAVAILABLE
    return abs(user_tokens - model_tokens) / user_tokens


def current_block(normalized: dict[str, Any], limit: int, top_n: int) -> dict[str, Any]:
    users_scope = scope_for_length(len(normalized["users"]), limit)
    models_scope = scope_for_length(len(normalized["models"]), limit)
    coverage_scope = weakest_scope([users_scope, models_scope])
    user_totals = aggregate_rows(normalized["users"], "users", users_scope)
    model_totals = aggregate_rows(normalized["models"], "models", models_scope)
    covered_totals = dict(user_totals)
    covered_totals.update(metric_meta(coverage_scope))
    covered_totals["model_total_diff_ratio"] = model_total_diff_ratio(user_totals, model_totals)
    return {
        "users": normalized["users"][:top_n],
        "models": normalized["models"][:top_n],
        "covered_totals": covered_totals,
        "users_scope": users_scope,
        "models_scope": models_scope,
        "coverage_scope": coverage_scope,
    }


def history_period_block(
    period_end_at: str,
    normalized: dict[str, Any],
    limit: int,
) -> dict[str, Any]:
    users_scope = scope_for_length(len(normalized["users"]), limit)
    models_scope = scope_for_length(len(normalized["models"]), limit)
    coverage_scope = weakest_scope([users_scope, models_scope])
    totals = aggregate_rows(normalized["users"], "users", users_scope)
    totals.update(metric_meta(coverage_scope))
    return {
        "period_end_at": period_end_at,
        "scope": coverage_scope,
        "confidence": confidence_for_scope(coverage_scope),
        "covered_totals": totals,
    }


def average_available(periods: list[dict[str, Any]], field: str) -> int | float | str:
    values = [numeric_or_none(period["covered_totals"].get(field)) for period in periods]
    usable = [value for value in values if value is not None]
    if not usable:
        return UNAVAILABLE
    avg = sum(usable) / len(usable)
    return int(avg) if avg.is_integer() else avg


def first_available(periods: list[dict[str, Any]], field: str) -> int | float | str:
    for period in periods:
        value = period["covered_totals"].get(field)
        if numeric_or_none(value) is not None:
            return value
    return UNAVAILABLE


def earliest_position_value(periods_by_position: list[dict[str, Any] | None], field: str) -> int | float | str:
    if not periods_by_position:
        return UNAVAILABLE
    earliest = periods_by_position[-1]
    if earliest is None:
        return UNAVAILABLE
    value = earliest["covered_totals"].get(field)
    return value if is_available(value) else UNAVAILABLE


def values_for_positions(periods_by_position: list[dict[str, Any] | None], positions: Iterable[int], field: str) -> list[float] | None:
    values: list[float] = []
    for position in positions:
        if position < 0 or position >= len(periods_by_position):
            return None
        period = periods_by_position[position]
        if period is None:
            return None
        value = numeric_or_none(period["covered_totals"].get(field))
        if value is None:
            return None
        values.append(value)
    return values


def last_3_vs_first_3_by_position(periods_by_position: list[dict[str, Any] | None], field: str) -> float | str:
    if len(periods_by_position) < 6:
        return UNAVAILABLE
    first_values = values_for_positions(periods_by_position, range(len(periods_by_position) - 3, len(periods_by_position)), field)
    last_values = values_for_positions(periods_by_position, range(0, 3), field)
    if first_values is None or last_values is None:
        return UNAVAILABLE
    # history_periods are newest first, so first_values are earliest windows.
    first_avg = sum(first_values) / 3
    last_avg = sum(last_values) / 3
    return percent_change(last_avg, first_avg)


def partial_window_coverage_ok(periods_by_position: list[dict[str, Any] | None], requested: int) -> bool:
    if requested <= 0:
        return False
    success_positions = [index for index, period in enumerate(periods_by_position[:requested]) if period is not None]
    if len(success_positions) < max(3, math.ceil(requested * 0.7)):
        return False
    covered_span = max(success_positions) - min(success_positions) + 1 if success_positions else 0
    return covered_span >= math.ceil(requested * 0.7)


def coefficient_of_variation(periods: list[dict[str, Any]], field: str) -> float | str:
    values = [numeric_or_none(period["covered_totals"].get(field)) for period in periods]
    usable = [value for value in values if value is not None]
    if len(usable) < 2:
        return UNAVAILABLE
    mean = sum(usable) / len(usable)
    if mean == 0:
        return UNAVAILABLE
    variance = sum((value - mean) ** 2 for value in usable) / len(usable)
    return math.sqrt(variance) / mean


def coefficient_of_variation_by_position(
    periods_by_position: list[dict[str, Any] | None],
    field: str,
    requested: int,
) -> float | str:
    if not partial_window_coverage_ok(periods_by_position, requested):
        return UNAVAILABLE
    return coefficient_of_variation([period for period in periods_by_position[:requested] if period is not None], field)


def slope_percent_per_workday_by_position(
    periods_by_position: list[dict[str, Any] | None],
    field: str,
    requested: int,
) -> float | str:
    if not partial_window_coverage_ok(periods_by_position, requested):
        return UNAVAILABLE
    chronological = list(reversed(periods_by_position[:requested]))
    points: list[tuple[int, float]] = []
    for index, period in enumerate(chronological):
        if period is None:
            continue
        value = numeric_or_none(period["covered_totals"].get(field))
        if value is not None:
            points.append((index, value))
    if len(points) < 2:
        return UNAVAILABLE
    mean_x = sum(point[0] for point in points) / len(points)
    mean_y = sum(point[1] for point in points) / len(points)
    if mean_y == 0:
        return UNAVAILABLE
    denominator = sum((point[0] - mean_x) ** 2 for point in points)
    if denominator == 0:
        return UNAVAILABLE
    slope = sum((point[0] - mean_x) * (point[1] - mean_y) for point in points) / denominator
    return maybe_number((slope / mean_y) * 100.0)


def standard_deviation(periods: list[dict[str, Any]], field: str) -> float | str:
    values = [numeric_or_none(period["covered_totals"].get(field)) for period in periods]
    usable = [value for value in values if value is not None]
    if len(usable) < 2:
        return UNAVAILABLE
    mean = sum(usable) / len(usable)
    variance = sum((value - mean) ** 2 for value in usable) / len(usable)
    return math.sqrt(variance)


def classify_token_trend(
    latest_vs_avg: Any,
    last3_first3: Any,
    volatility: Any,
    slope: Any,
    current_value: Any,
    baseline_average: Any,
    baseline_stddev: Any,
    scope: str,
) -> dict[str, Any]:
    latest = numeric_or_none(latest_vs_avg)
    last = numeric_or_none(last3_first3)
    cv = numeric_or_none(volatility)
    slope_value = numeric_or_none(slope)
    current = numeric_or_none(current_value)
    average = numeric_or_none(baseline_average)
    stddev = numeric_or_none(baseline_stddev)
    labels: list[str] = []
    primary = "unknown"
    if (
        current is not None
        and average is not None
        and stddev is not None
        and current > average + 2 * stddev
        and current - average >= 1_000_000
    ):
        primary = "spike"
        labels.append("current_spike")
    elif cv is not None and cv >= 0.5:
        primary = "volatile"
        labels.append("volatile")
    elif last is not None and last >= 15 and slope_value is not None and slope_value > 0:
        primary = "up"
    elif last is not None and last <= -15 and slope_value is not None and slope_value < 0:
        primary = "down"
    elif last is not None and abs(last) < 10 and cv is not None and cv < 0.2:
        primary = "flat"
    if latest is not None and latest >= 50:
        labels.append("current_above_baseline")
    return {"primary": primary, "labels": labels, "metric": "total_tokens", **metric_meta(scope)}


def concentration(users: list[dict[str, Any]], totals: dict[str, Any], scope: str) -> dict[str, Any]:
    if scope != SCOPE_WITHIN_ENDPOINT_LIMIT:
        return {"label": "suppressed_when_limit_boundary_unknown", "scope": scope, "confidence": "low"}
    total_tokens = numeric_or_none(totals.get("total_tokens"))
    if not total_tokens:
        return {"label": "unknown", "scope": scope, "confidence": "low"}
    shares = []
    for count in (1, 3, 10):
        subtotal = sum(numeric_or_none(row.get("total_tokens")) or 0 for row in users[:count])
        shares.append((count, subtotal / total_tokens))
    top1 = shares[0][1]
    top3 = shares[1][1]
    top10 = shares[2][1]
    if top1 > 0.5 or top3 > 0.75:
        label = "highly_concentrated"
    elif top3 > 0.5:
        label = "moderately_concentrated"
    elif top10 <= 0.7:
        label = "distributed"
    else:
        label = "mixed"
    return {"label": label, "top_1_share": top1, "top_3_share": top3, "top_10_share": top10, "scope": scope, "confidence": "high"}


def identity_confidence_for_sources(sources: Iterable[str]) -> str:
    source_set = set(sources)
    if not source_set:
        return "low"
    if source_set <= {"stable_id"}:
        return "high"
    if source_set <= {"stable_id", "id_fingerprint"} and "id_fingerprint" in source_set:
        return "medium"
    return "low"


def entity_confidence_for_source(source: str, scope: str) -> str:
    if scope != SCOPE_WITHIN_ENDPOINT_LIMIT:
        return "low"
    if source == "stable_id":
        return "high"
    if source == "id_fingerprint":
        return "medium"
    return "low"


def can_join_user_source(source: str) -> bool:
    return source in {"stable_id", "id_fingerprint", "name_fingerprint"}


def user_insight_item(
    *,
    user_key: str,
    display_name: str,
    metric: str,
    current: Any,
    baseline: Any,
    percent: Any,
    score: Any,
    scope: str,
    confidence: str,
    reason: str,
    history_evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "user": user_key,
        "display_name": display_name,
        "metric": metric,
        "current": current,
        "baseline": baseline,
        "history_evidence": history_evidence,
        "percent_change": percent,
        "score": score,
        "scope": scope,
        "confidence": confidence,
        "reason": reason,
    }


def average_numbers(values: Iterable[float]) -> float | str:
    usable = list(values)
    if not usable:
        return UNAVAILABLE
    average = sum(usable) / len(usable)
    return int(average) if average.is_integer() else average


def cv_numbers(values: list[float]) -> float | str:
    if len(values) < 2:
        return UNAVAILABLE
    mean = sum(values) / len(values)
    if mean == 0:
        return UNAVAILABLE
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance) / mean


def user_rows_by_key(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["entity_key"]: row for row in rows}


def build_user_series(
    current_users: list[dict[str, Any]],
    history_users_by_position: list[list[dict[str, Any]] | None],
    current_users_scope: str,
    history_user_scopes_by_position: list[str | None],
    trend_workdays: int,
) -> tuple[dict[str, dict[str, Any]], str, str, bool]:
    sources = [
        row.get("entity_key_source", "")
        for rows in [current_users] + [rows or [] for rows in history_users_by_position[:trend_workdays]]
        for row in rows
        if row.get("entity_type") == "user"
    ]
    identity_confidence = identity_confidence_for_sources(sources)
    identity_warning = identity_confidence == "low"
    scope = weakest_scope(
        [current_users_scope]
        + [item for item in history_user_scopes_by_position[:trend_workdays] if item is not None]
    )
    zero_fill_allowed = (
        scope == SCOPE_WITHIN_ENDPOINT_LIMIT
        and len(history_users_by_position[:trend_workdays]) == trend_workdays
        and all(rows is not None for rows in history_users_by_position[:trend_workdays])
    )
    current_by_key = user_rows_by_key(current_users)
    history_maps = [user_rows_by_key(rows) if rows is not None else None for rows in history_users_by_position[:trend_workdays]]
    keys = set(current_by_key)
    for row_map in history_maps:
        if row_map is not None:
            keys.update(row_map)
    series: dict[str, dict[str, Any]] = {}
    for key in sorted(keys):
        candidate_rows = []
        if key in current_by_key:
            candidate_rows.append(current_by_key[key])
        for row_map in history_maps:
            if row_map is not None and key in row_map:
                candidate_rows.append(row_map[key])
        if not candidate_rows:
            continue
        key_source = str(candidate_rows[0].get("entity_key_source", "display_name_fingerprint"))
        display_name = str(candidate_rows[0].get("display_name", "unknown"))
        if key in current_by_key:
            display_name = str(current_by_key[key].get("display_name", display_name))
            key_source = str(current_by_key[key].get("entity_key_source", key_source))
        history_values: list[float | None] = []
        observed_history_windows = 0
        active_history_windows = 0
        unavailable_numeric = False
        for row_map in history_maps:
            if row_map is None:
                history_values.append(None)
                continue
            row = row_map.get(key)
            if row is None:
                history_values.append(0.0 if zero_fill_allowed else None)
                continue
            value = numeric_or_none(row.get("total_tokens"))
            if value is None:
                unavailable_numeric = True
                history_values.append(None)
                continue
            observed_history_windows += 1
            if value > 0:
                active_history_windows += 1
            history_values.append(value)
        if key in current_by_key:
            current_value = numeric_or_none(current_by_key[key].get("total_tokens"))
        else:
            current_value = 0.0 if zero_fill_allowed else None
        series[key] = {
            "user": key,
            "display_name": display_name,
            "entity_key_source": key_source,
            "current": current_value,
            "history_values": history_values,
            "observed_history_windows": observed_history_windows,
            "active_history_windows": active_history_windows,
            "zero_fill_applied": zero_fill_allowed,
            "unavailable_numeric": unavailable_numeric,
            "can_join": can_join_user_source(key_source),
            "confidence": entity_confidence_for_source(key_source, scope),
            "scope": scope,
        }
    return series, identity_confidence, scope, identity_warning


def positioned_values(series_item: dict[str, Any], include_current: bool) -> list[float | None]:
    values = list(series_item["history_values"])
    if include_current:
        values = [series_item["current"]] + values
    return values


def first_n(values: list[float | None], count: int) -> list[float] | None:
    selected = values[:count]
    if len(selected) < count or any(value is None for value in selected):
        return None
    return [float(value) for value in selected]


def last_n(values: list[float | None], count: int) -> list[float] | None:
    selected = values[-count:]
    if len(selected) < count or any(value is None for value in selected):
        return None
    return [float(value) for value in selected]


def user_trend_lists(
    series: dict[str, dict[str, Any]],
    top_n: int,
    scope: str,
    identity_confidence: str,
) -> dict[str, list[dict[str, Any]]]:
    rising: list[dict[str, Any]] = []
    falling: list[dict[str, Any]] = []
    newly_active: list[dict[str, Any]] = []
    volatile: list[dict[str, Any]] = []
    advisory: list[dict[str, Any]] = []
    for item in series.values():
        if not item["can_join"]:
            continue
        values_with_current = positioned_values(item, include_current=True)
        usable_history = [value for value in item["history_values"] if value is not None]
        baseline = average_numbers(float(value) for value in usable_history)
        current = item["current"] if item["current"] is not None else UNAVAILABLE
        percent = percent_change(current, baseline)
        history_evidence = {
            "observed_history_windows": item["observed_history_windows"],
            "active_history_windows": item["active_history_windows"],
            "zero_fill_applied": item["zero_fill_applied"],
            "entity_key_source": item["entity_key_source"],
        }
        usable_with_current = [value for value in values_with_current if value is not None]
        cv = cv_numbers([float(value) for value in usable_with_current])
        strong_allowed = (
            identity_confidence != "low"
            and item["confidence"] != "low"
            and item["zero_fill_applied"]
            and not item["unavailable_numeric"]
        )
        recent = first_n(values_with_current, 3)
        earliest = last_n(values_with_current, 3)
        recent_avg = average_numbers(recent) if recent is not None else UNAVAILABLE
        earliest_avg = average_numbers(earliest) if earliest is not None else UNAVAILABLE
        recent_vs_earliest = percent_change(recent_avg, earliest_avg)
        reason = "identity_or_scope_downgraded_to_advisory" if not strong_allowed else "strong_identity_and_complete_scope"
        base_item = user_insight_item(
            user_key=item["user"],
            display_name=item["display_name"],
            metric="total_tokens",
            current=current,
            baseline=baseline,
            percent=percent,
            score=cv if cv != UNAVAILABLE else recent_vs_earliest,
            scope=scope,
            confidence=item["confidence"],
            reason=reason,
            history_evidence={**history_evidence, "recent_3_average": recent_avg, "earliest_3_average": earliest_avg},
        )
        trend_detected = False
        if cv != UNAVAILABLE and float(cv) >= 0.5 and len(usable_with_current) >= 5:
            trend_detected = True
            volatile_item = dict(base_item)
            volatile_item["reason"] = "coefficient_of_variation_ge_0.5" if strong_allowed else "volatile_downgraded_to_advisory"
            if strong_allowed:
                volatile.append(volatile_item)
            else:
                advisory.append(volatile_item)
        if recent_vs_earliest != UNAVAILABLE:
            percent_value = float(recent_vs_earliest)
            if percent_value >= 30 and numeric_or_none(recent_avg) is not None and float(recent_avg) >= 1_000_000:
                trend_detected = True
                trend_item = dict(base_item)
                trend_item["percent_change"] = recent_vs_earliest
                trend_item["score"] = recent_vs_earliest
                trend_item["reason"] = "recent_3_up_at_least_30_percent" if strong_allowed else "rising_downgraded_to_advisory"
                if strong_allowed:
                    rising.append(trend_item)
                else:
                    advisory.append(trend_item)
            if percent_value <= -30 and numeric_or_none(earliest_avg) is not None and float(earliest_avg) >= 1_000_000:
                trend_detected = True
                trend_item = dict(base_item)
                trend_item["percent_change"] = recent_vs_earliest
                trend_item["score"] = abs(percent_value)
                trend_item["reason"] = "recent_3_down_at_least_30_percent" if strong_allowed else "falling_downgraded_to_advisory"
                if strong_allowed:
                    falling.append(trend_item)
                else:
                    advisory.append(trend_item)
        if strong_allowed and len(values_with_current) >= 6:
            early_half = values_with_current[len(values_with_current) // 2 :]
            recent_values = values_with_current[:3]
            if all(value is not None for value in early_half + recent_values):
                early_avg = sum(float(value) for value in early_half) / len(early_half)
                recent_active = sum(1 for value in recent_values if float(value) > 0)
                if early_avg == 0 and recent_active >= 2:
                    newly_active.append(
                        user_insight_item(
                            user_key=item["user"],
                            display_name=item["display_name"],
                            metric="total_tokens",
                            current=current,
                            baseline=0,
                            percent=UNAVAILABLE,
                            score=recent_active,
                            scope=scope,
                            confidence=item["confidence"],
                            reason="early_half_zero_recent_3_active",
                            history_evidence={**history_evidence, "recent_active_windows": recent_active, "early_half_average": 0},
                        )
                    )
        elif not strong_allowed and not trend_detected and usable_with_current:
            advisory.append(base_item)
    sort_key = lambda row: numeric_or_none(row.get("score")) or 0
    return {
        "volatile_users": sorted(volatile, key=sort_key, reverse=True)[:top_n],
        "rising_users": sorted(rising, key=sort_key, reverse=True)[:top_n],
        "falling_users": sorted(falling, key=sort_key, reverse=True)[:top_n],
        "newly_active_users": sorted(newly_active, key=sort_key, reverse=True)[:top_n],
        "advisory_user_trends": sorted(advisory, key=sort_key, reverse=True)[:top_n],
    }


def heavy_user_items(
    current_users: list[dict[str, Any]],
    series: dict[str, dict[str, Any]],
    totals: dict[str, Any],
    scope: str,
    top_n: int,
) -> list[dict[str, Any]]:
    total_tokens = numeric_or_none(totals.get("total_tokens"))
    items: list[dict[str, Any]] = []
    for row in current_users[:top_n]:
        current = row.get("total_tokens") if is_available(row.get("total_tokens")) else UNAVAILABLE
        series_item = series.get(row["entity_key"], {})
        baseline = UNAVAILABLE
        if series_item:
            usable_history = [value for value in series_item.get("history_values", []) if value is not None]
            baseline = average_numbers(float(value) for value in usable_history)
        share = safe_divide(current, total_tokens)
        items.append(
            user_insight_item(
                user_key=row["entity_key"],
                display_name=row["display_name"],
                metric="total_tokens",
                current=current,
                baseline=baseline,
                percent=percent_change(current, baseline),
                score=share if share is not None else UNAVAILABLE,
                scope=scope,
                confidence=confidence_for_scope(scope),
                reason="current_window_top_user",
                history_evidence={
                    "current_rank": row.get("rank"),
                    "current_share": share if share is not None else UNAVAILABLE,
                    "observed_history_windows": series_item.get("observed_history_windows", 0),
                    "zero_fill_applied": series_item.get("zero_fill_applied", False),
                },
            )
        )
    return items


def user_insights_block(
    current_users: list[dict[str, Any]],
    history_users_by_position: list[list[dict[str, Any]] | None],
    current_totals: dict[str, Any],
    current_users_scope: str,
    history_user_scopes_by_position: list[str | None],
    trend_workdays: int,
    top_n: int,
) -> dict[str, Any]:
    series, identity_confidence, user_scope, identity_warning = build_user_series(
        current_users,
        history_users_by_position,
        current_users_scope,
        history_user_scopes_by_position,
        trend_workdays,
    )
    trends = user_trend_lists(series, top_n, user_scope, identity_confidence)
    return {
        "identity_confidence": identity_confidence,
        "heavy_users": heavy_user_items(current_users, series, current_totals, current_users_scope, top_n),
        **trends,
        "concentration": concentration(current_users, current_totals, current_users_scope),
        "identity_warning": identity_warning,
    }


def row_has_token_component_quality_warning(row: dict[str, Any]) -> bool:
    quality = row.get("data_quality") if isinstance(row.get("data_quality"), dict) else {}
    missing = set(quality.get("missing_fields") or [])
    nulls = set(quality.get("null_fields") or [])
    derived = set(quality.get("derived_fields") or [])
    required = {"total_tokens", *TOKEN_COMPONENT_FIELDS}
    return bool(required & (missing | nulls | derived))


def token_component_sum(totals: dict[str, Any]) -> float | None:
    values = [numeric_or_none(totals.get(field)) for field in TOKEN_COMPONENT_FIELDS]
    if any(value is None for value in values):
        return None
    return sum(float(value) for value in values if value is not None)


def token_denominator(totals: dict[str, Any], rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    total_tokens = numeric_or_none(totals.get("total_tokens"))
    component_sum = token_component_sum(totals)
    quality_warning = any(row_has_token_component_quality_warning(row) for row in rows or [])
    if total_tokens is None:
        return {
            "denominator": None,
            "denominator_source": UNAVAILABLE,
            "component_sum": component_sum if component_sum is not None else UNAVAILABLE,
            "token_component_warning": True,
            "unavailable_reason": "total_tokens_unavailable",
        }
    if component_sum is None:
        return {
            "denominator": None,
            "denominator_source": UNAVAILABLE,
            "component_sum": UNAVAILABLE,
            "token_component_warning": True,
            "unavailable_reason": "token_component_unavailable",
        }
    if total_tokens == 0 or component_sum == 0:
        return {
            "denominator": None,
            "denominator_source": UNAVAILABLE,
            "component_sum": component_sum,
            "token_component_warning": True,
            "unavailable_reason": "zero_token_denominator",
        }
    diff = abs(component_sum - total_tokens)
    tolerance = max(1.0, abs(total_tokens) * 0.01)
    if quality_warning or diff > tolerance:
        return {
            "denominator": component_sum,
            "denominator_source": "component_sum",
            "component_sum": component_sum,
            "token_component_warning": True,
            "unavailable_reason": None,
        }
    return {
        "denominator": total_tokens,
        "denominator_source": "total_tokens",
        "component_sum": component_sum,
        "token_component_warning": False,
        "unavailable_reason": None,
    }


def token_structure(totals: dict[str, Any], rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    denominator_meta = token_denominator(totals, rows)
    denominator = denominator_meta["denominator"]
    ratios: dict[str, Any] = {}
    mapping = {
        "cache_read_ratio": "cache_read_tokens",
        "input_ratio": "input_tokens",
        "output_ratio": "output_tokens",
        "cache_creation_ratio": "cache_creation_tokens",
    }
    for output_key, source_key in mapping.items():
        ratio = safe_divide(totals.get(source_key), denominator)
        ratios[output_key] = ratio if ratio is not None else UNAVAILABLE
    fresh_tokens = (
        sum(numeric_or_none(totals.get(field)) or 0 for field in ("input_tokens", "output_tokens", "cache_creation_tokens"))
        if denominator is not None
        and all(numeric_or_none(totals.get(field)) is not None for field in ("input_tokens", "output_tokens", "cache_creation_tokens"))
        else UNAVAILABLE
    )
    fresh_ratio = safe_divide(fresh_tokens, denominator)
    ratios["fresh_token_ratio"] = fresh_ratio if fresh_ratio is not None else UNAVAILABLE
    available = {key: numeric_or_none(value) for key, value in ratios.items() if numeric_or_none(value) is not None}
    driver_map = {
        "cache_read_ratio": "cache_read",
        "input_ratio": "input",
        "output_ratio": "output",
        "cache_creation_ratio": "cache_creation",
        "fresh_token_ratio": "fresh",
    }
    component_available = {key: value for key, value in available.items() if key != "fresh_token_ratio"}
    ratios["dominant_driver"] = driver_map[max(component_available, key=lambda key: component_available[key])] if component_available else "mixed"
    ratios["component_sum"] = maybe_number(denominator_meta["component_sum"]) if denominator_meta["component_sum"] != UNAVAILABLE else UNAVAILABLE
    ratios["denominator_source"] = denominator_meta["denominator_source"]
    ratios["token_component_warning"] = denominator_meta["token_component_warning"]
    if denominator_meta["unavailable_reason"]:
        ratios["unavailable_reason"] = denominator_meta["unavailable_reason"]
    return ratios


def model_rows_by_name(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("display_name", "unknown"))
        if name not in grouped:
            grouped[name] = {
                "model": name,
                "display_name": name,
                "rank": row.get("rank"),
                "data_quality": {"missing_fields": [], "null_fields": [], "derived_fields": []},
            }
            for field in NUMERIC_FIELDS:
                grouped[name][field] = 0.0
        target = grouped[name]
        target_rank = numeric_or_none(target.get("rank"))
        row_rank = numeric_or_none(row.get("rank"))
        if target_rank is None:
            target["rank"] = row.get("rank")
        elif row_rank is not None:
            target["rank"] = min(target_rank, row_rank)
        quality = row.get("data_quality") if isinstance(row.get("data_quality"), dict) else {}
        for key in ("missing_fields", "null_fields", "derived_fields"):
            target["data_quality"][key] = sorted(set(target["data_quality"][key]) | set(quality.get(key) or []))
        for field in NUMERIC_FIELDS:
            value = numeric_or_none(row.get(field))
            if value is None or target[field] == UNAVAILABLE:
                target[field] = UNAVAILABLE
            else:
                target[field] += value
    for row in grouped.values():
        for field in NUMERIC_FIELDS:
            value = row.get(field)
            if isinstance(value, float) and value.is_integer():
                row[field] = int(value)
    return grouped


def model_history_scope(
    current_models_scope: str,
    history_model_scopes_by_position: list[str | None],
    trend_workdays: int,
) -> str:
    return weakest_scope([current_models_scope] + [item for item in history_model_scopes_by_position[:trend_workdays] if item is not None])


def cost_available_for_model_insights(
    current_models: list[dict[str, Any]],
    history_models_by_position: list[list[dict[str, Any]] | None],
    trend_workdays: int,
) -> bool:
    all_rows = list(current_models)
    for rows in history_models_by_position[:trend_workdays]:
        if rows is None:
            return False
        all_rows.extend(rows)
    return bool(all_rows) and all(is_available(row.get("cost")) for row in all_rows)


def model_insight_item(
    *,
    model: str,
    metric: str,
    current: Any,
    baseline: Any,
    delta: Any,
    percent: Any,
    share_change: Any,
    scope: str,
    confidence: str,
    reason: str,
    history_evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "model": model,
        "metric": metric,
        "current": current,
        "baseline": baseline,
        "history_evidence": history_evidence,
        "delta": delta,
        "percent_change": percent,
        "share_change": share_change,
        "scope": scope,
        "confidence": confidence,
        "reason": reason,
    }


def available_delta(current: Any, baseline: Any) -> int | float | str:
    left = numeric_or_none(current)
    right = numeric_or_none(baseline)
    if left is None or right is None:
        return UNAVAILABLE
    delta = left - right
    return int(delta) if delta.is_integer() else delta


def average_optional(values: Iterable[float | None]) -> int | float | str:
    usable = [value for value in values if value is not None]
    return average_numbers(usable)


def model_metric_per_request(row: dict[str, Any] | None, numerator_field: str) -> float | None:
    if row is None:
        return None
    return safe_divide(row.get(numerator_field), row.get("requests"))


def model_cost_per_million(row: dict[str, Any] | None) -> float | None:
    if row is None:
        return None
    ratio = safe_divide(row.get("cost"), row.get("total_tokens"))
    return ratio * 1_000_000 if ratio is not None else None


def model_insights_block(
    current_models: list[dict[str, Any]],
    history_models_by_position: list[list[dict[str, Any]] | None],
    current_models_scope: str,
    history_model_scopes_by_position: list[str | None],
    trend_workdays: int,
    top_n: int,
) -> dict[str, Any]:
    scope = model_history_scope(current_models_scope, history_model_scopes_by_position, trend_workdays)
    confidence = confidence_for_scope(scope)
    zero_fill_allowed = (
        scope == SCOPE_WITHIN_ENDPOINT_LIMIT
        and len(history_models_by_position[:trend_workdays]) == trend_workdays
        and all(rows is not None for rows in history_models_by_position[:trend_workdays])
    )
    current_by_name = model_rows_by_name(current_models)
    history_maps = [model_rows_by_name(rows) if rows is not None else None for rows in history_models_by_position[:trend_workdays]]
    current_totals = aggregate_rows(list(current_by_name.values()), "models", current_models_scope)
    history_totals = [
        aggregate_rows(list(row_map.values()), "models", history_model_scopes_by_position[index] or scope)
        if row_map is not None
        else None
        for index, row_map in enumerate(history_maps)
    ]
    keys = set(current_by_name)
    for row_map in history_maps:
        if row_map is not None:
            keys.update(row_map)

    top_models: list[dict[str, Any]] = []
    share_changes: list[dict[str, Any]] = []
    new_or_missing: list[dict[str, Any]] = []
    cache_heavy: list[dict[str, Any]] = []
    request_intensity: list[dict[str, Any]] = []
    expensive: list[dict[str, Any]] = []
    cost_available = cost_available_for_model_insights(current_models, history_models_by_position, trend_workdays)

    for name in sorted(keys):
        current_row = current_by_name.get(name)
        current_tokens = current_row.get("total_tokens") if current_row and is_available(current_row.get("total_tokens")) else (0 if zero_fill_allowed else UNAVAILABLE)
        current_share = safe_divide(current_tokens, current_totals.get("total_tokens"))
        history_token_values: list[float | None] = []
        history_shares: list[float | None] = []
        observed_history_windows = 0
        active_history_windows = 0
        missing_history_positions: list[int] = []
        for index, row_map in enumerate(history_maps):
            if row_map is None:
                history_token_values.append(None)
                history_shares.append(None)
                missing_history_positions.append(index)
                continue
            row = row_map.get(name)
            if row is None:
                value = 0.0 if zero_fill_allowed else None
                share = 0.0 if zero_fill_allowed else None
                if not zero_fill_allowed:
                    missing_history_positions.append(index)
            else:
                value = numeric_or_none(row.get("total_tokens"))
                share = safe_divide(row.get("total_tokens"), history_totals[index].get("total_tokens") if history_totals[index] else UNAVAILABLE)
                observed_history_windows += 1 if value is not None else 0
                active_history_windows += 1 if value is not None and value > 0 else 0
            history_token_values.append(value)
            history_shares.append(share)
        baseline_tokens = average_optional(history_token_values)
        baseline_share = average_optional(history_shares)
        share_change = available_delta(current_share if current_share is not None else UNAVAILABLE, baseline_share)
        evidence = {
            "current_rank": current_row.get("rank") if current_row else UNAVAILABLE,
            "observed_history_windows": observed_history_windows,
            "active_history_windows": active_history_windows,
            "missing_history_positions": missing_history_positions,
            "zero_fill_applied": zero_fill_allowed,
            "join_key": "model_name",
        }
        item = model_insight_item(
            model=name,
            metric="total_tokens",
            current=current_tokens,
            baseline=baseline_tokens,
            delta=available_delta(current_tokens, baseline_tokens),
            percent=percent_change(current_tokens, baseline_tokens),
            share_change=share_change,
            scope=scope,
            confidence=confidence,
            reason="current_window_top_model" if current_row else "historically_observed_model",
            history_evidence={**evidence, "current_share": current_share if current_share is not None else UNAVAILABLE, "baseline_share": baseline_share},
        )
        if current_row is not None:
            top_models.append(item)
            structure = token_structure(current_row, [current_row])
            dominant = structure.get("dominant_driver")
            cache_read_ratio = numeric_or_none(structure.get("cache_read_ratio"))
            cache_creation_ratio = numeric_or_none(structure.get("cache_creation_ratio"))
            if (cache_read_ratio is not None and cache_read_ratio >= 0.5) or (
                cache_creation_ratio is not None and cache_creation_ratio >= 0.2
            ):
                cache_heavy.append(
                    model_insight_item(
                        model=name,
                        metric="token_component_ratio",
                        current=max(cache_read_ratio or 0, cache_creation_ratio or 0),
                        baseline=UNAVAILABLE,
                        delta=UNAVAILABLE,
                        percent=UNAVAILABLE,
                        share_change=share_change,
                        scope=current_models_scope,
                        confidence=confidence_for_scope(current_models_scope),
                        reason=f"{dominant}_dominant_model",
                        history_evidence={**evidence, "token_structure": structure},
                    )
                )
        if share_change != UNAVAILABLE:
            share_changes.append(
                model_insight_item(
                    model=name,
                    metric="total_token_share",
                    current=current_share if current_share is not None else UNAVAILABLE,
                    baseline=baseline_share,
                    delta=share_change,
                    percent=percent_change(current_share if current_share is not None else UNAVAILABLE, baseline_share),
                    share_change=share_change,
                    scope=scope,
                    confidence=confidence,
                    reason="model_share_changed",
                    history_evidence=evidence,
                )
            )
        if current_row is not None and observed_history_windows == 0:
            new_or_missing.append(
                model_insight_item(
                    model=name,
                    metric="model_presence",
                    current=current_share if current_share is not None else UNAVAILABLE,
                    baseline=0 if zero_fill_allowed else UNAVAILABLE,
                    delta=current_share if current_share is not None and zero_fill_allowed else UNAVAILABLE,
                    percent=UNAVAILABLE,
                    share_change=current_share if current_share is not None and zero_fill_allowed else UNAVAILABLE,
                    scope=scope,
                    confidence=confidence,
                    reason="observed_current_not_in_history",
                    history_evidence=evidence,
                )
            )
        if current_row is None and observed_history_windows > 0:
            new_or_missing.append(
                model_insight_item(
                    model=name,
                    metric="model_presence",
                    current=0 if zero_fill_allowed else UNAVAILABLE,
                    baseline=baseline_share,
                    delta=available_delta(0, baseline_share) if zero_fill_allowed else UNAVAILABLE,
                    percent=UNAVAILABLE,
                    share_change=available_delta(0, baseline_share) if zero_fill_allowed else UNAVAILABLE,
                    scope=scope,
                    confidence=confidence,
                    reason="not_observed_current_ranked_rows" if not zero_fill_allowed else "missing_from_current_complete_scope",
                    history_evidence=evidence,
                )
            )

        if current_row is not None:
            intensity_specs = [
                ("tokens_per_request", "total_tokens", False),
                ("output_tokens_per_request", "output_tokens", False),
                ("cost_per_request", "cost", True),
            ]
            for metric, numerator, needs_cost in intensity_specs:
                if needs_cost and not cost_available:
                    continue
                current_value = model_metric_per_request(current_row, numerator)
                history_values = [
                    model_metric_per_request(row_map.get(name), numerator) if row_map is not None and name in row_map else None
                    for row_map in history_maps
                ]
                baseline_value = average_optional(history_values)
                percent = percent_change(current_value if current_value is not None else UNAVAILABLE, baseline_value)
                if current_value is None and baseline_value == UNAVAILABLE:
                    continue
                request_intensity.append(
                    model_insight_item(
                        model=name,
                        metric=metric,
                        current=current_value if current_value is not None else UNAVAILABLE,
                        baseline=baseline_value,
                        delta=available_delta(current_value if current_value is not None else UNAVAILABLE, baseline_value),
                        percent=percent,
                        share_change=share_change,
                        scope=scope,
                        confidence=confidence,
                        reason="model_request_intensity",
                        history_evidence=evidence,
                    )
                )
            if cost_available:
                current_cpm = model_cost_per_million(current_row)
                history_cpm = [
                    model_cost_per_million(row_map.get(name)) if row_map is not None and name in row_map else None
                    for row_map in history_maps
                ]
                baseline_cpm = average_optional(history_cpm)
                expensive.append(
                    model_insight_item(
                        model=name,
                        metric="cost_per_million_tokens",
                        current=current_cpm if current_cpm is not None else UNAVAILABLE,
                        baseline=baseline_cpm,
                        delta=available_delta(current_cpm if current_cpm is not None else UNAVAILABLE, baseline_cpm),
                        percent=percent_change(current_cpm if current_cpm is not None else UNAVAILABLE, baseline_cpm),
                        share_change=share_change,
                        scope=scope,
                        confidence=confidence,
                        reason="model_cost_intensity",
                        history_evidence={
                            **evidence,
                            "cost_share": safe_divide(current_row.get("cost"), current_totals.get("cost")) or UNAVAILABLE,
                            "cost_unit": "original",
                        },
                    )
                )

    score_abs = lambda row: abs(numeric_or_none(row.get("share_change")) or numeric_or_none(row.get("delta")) or 0)
    score_current = lambda row: numeric_or_none(row.get("current")) or 0
    return {
        "scope": scope,
        "confidence": confidence,
        "top_models": sorted(top_models, key=score_current, reverse=True)[:top_n],
        "share_changes": sorted(share_changes, key=score_abs, reverse=True)[:top_n],
        "expensive_models": sorted(expensive, key=score_current, reverse=True)[:top_n] if cost_available else [],
        "cost_metrics_suppressed": not cost_available,
        "new_or_missing_models": sorted(new_or_missing, key=score_current, reverse=True)[:top_n],
        "cache_heavy_models": sorted(cache_heavy, key=score_current, reverse=True)[:top_n],
        "request_intensity_changes": sorted(request_intensity, key=lambda row: abs(numeric_or_none(row.get("percent_change")) or 0), reverse=True)[:top_n],
    }


def has_cost_available(
    current_totals: dict[str, Any],
    baseline_periods_by_position: list[dict[str, Any] | None],
    baseline_avg_cost: Any,
) -> bool:
    if not is_available(current_totals.get("cost")):
        return False
    if not is_available(baseline_avg_cost):
        return False
    return all(
        period is not None and is_available(period["covered_totals"].get("cost"))
        for period in baseline_periods_by_position
    )


def suppressed_cost_block(scope: str) -> dict[str, Any]:
    return {"suppressed": True, "reason": "cost_unavailable", **metric_meta(scope)}


def candidate_highlights(current_totals: dict[str, Any], baseline_avg_tokens: Any, scope: str) -> list[dict[str, Any]]:
    percent = percent_change(current_totals.get("total_tokens"), baseline_avg_tokens)
    if percent == UNAVAILABLE or abs(float(percent)) < 15:
        return []
    return [
        {
            "kind": "covered_total_tokens_vs_baseline",
            "current_value": current_totals.get("total_tokens"),
            "baseline_value": baseline_avg_tokens,
            "delta": (
                numeric_or_none(current_totals.get("total_tokens")) - numeric_or_none(baseline_avg_tokens)
                if numeric_or_none(current_totals.get("total_tokens")) is not None
                and numeric_or_none(baseline_avg_tokens) is not None
                else UNAVAILABLE
            ),
            "percent_delta": percent,
            "window": "current_vs_baseline",
            "scope": scope,
            "confidence": confidence_for_scope(scope),
            "evidence_fields": ["current.covered_totals.total_tokens", "baseline_comparison.company.total_tokens.average"],
        }
    ]


def redact_for_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: redact_for_json(item) for key, item in value.items() if not SENSITIVE_KEY_PATTERN.search(str(key))}
    if isinstance(value, list):
        return [redact_for_json(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return UNAVAILABLE
    if isinstance(value, str):
        return redact_text(value)
    return value


def thinking_number(value: Any) -> int | float:
    parsed = parse_numeric(value)
    return parsed if parsed is not None else 0


def thinking_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def sanitize_eligible_models(value: Any) -> str | list[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def sanitize_thinking_row(row: Any, fields: tuple[str, ...], text_fields: set[str]) -> dict[str, Any]:
    source = row if isinstance(row, dict) else {}
    return {
        field: thinking_text(source.get(field)) if field in text_fields else thinking_number(source.get(field))
        for field in fields
    }


def sanitize_thinking_user(row: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_thinking_row(row, THINKING_USER_FIELDS[:-1], {"user", "model"})
    levels_source = row.get("levels") if isinstance(row.get("levels"), list) else []
    sanitized["levels"] = [
        sanitize_thinking_row(level, THINKING_LEVEL_FIELDS, {"level"})
        for level in levels_source
        if isinstance(level, dict)
    ]
    return sanitized


def sanitize_thinking(value: Any) -> dict[str, Any]:
    """Allowlist current-window thinking statistics without retaining request data."""
    source = value if isinstance(value, dict) else {}
    coverage_source = source.get("coverage") if isinstance(source.get("coverage"), dict) else {}
    levels_source = source.get("levels") if isinstance(source.get("levels"), list) else []
    users_source = source.get("users") if isinstance(source.get("users"), list) else []
    return {
        "available": source.get("available") if isinstance(source.get("available"), bool) else False,
        "source": source.get("source") if isinstance(source.get("source"), str) else THINKING_SOURCE,
        "scope": source.get("scope") if isinstance(source.get("scope"), str) else THINKING_SCOPE,
        "eligible_models": sanitize_eligible_models(source.get("eligible_models")),
        "coverage": {field: thinking_number(coverage_source.get(field)) for field in THINKING_COVERAGE_FIELDS},
        "levels": [
            sanitize_thinking_row(row, THINKING_LEVEL_FIELDS, {"level"})
            for row in levels_source
            if isinstance(row, dict)
        ],
        "users": [
            sanitize_thinking_user(row)
            for row in users_source
            if isinstance(row, dict)
        ],
    }


def build_report(
    *,
    end_at: dt.datetime,
    hours: float,
    baseline_workdays: int,
    trend_workdays: int,
    limit: int,
    top_n: int,
    current_response: dict[str, Any],
    history_results: list[tuple[dt.datetime, FetchResult]],
    deadline_skipped: bool,
    max_stdout_bytes: int = 60_000,
) -> dict[str, Any]:
    normalized_current = normalize_response(current_response)
    thinking = sanitize_thinking(current_response.get("thinking"))
    current = current_block(normalized_current, limit, top_n)
    history_periods: list[dict[str, Any]] = []
    history_periods_by_position: list[dict[str, Any] | None] = []
    history_users_by_position: list[list[dict[str, Any]] | None] = []
    history_models_by_position: list[list[dict[str, Any]] | None] = []
    history_user_scopes_by_position: list[str | None] = []
    history_model_scopes_by_position: list[str | None] = []
    coverage_ledger: list[dict[str, Any]] = []
    missing_periods: list[dict[str, Any]] = []
    api_errors: list[dict[str, Any]] = []
    for period_end, result in history_results:
        period_end_at = isoformat_seconds(period_end)
        if result.ok and result.data is not None:
            normalized = normalize_response(result.data)
            users_scope = scope_for_length(len(normalized["users"]), limit)
            models_scope = scope_for_length(len(normalized["models"]), limit)
            period_block = history_period_block(period_end_at, normalized, limit)
            history_periods.append(period_block)
            history_periods_by_position.append(period_block)
            history_users_by_position.append(normalized["users"])
            history_models_by_position.append(normalized["models"])
            history_user_scopes_by_position.append(users_scope)
            history_model_scopes_by_position.append(models_scope)
            coverage_ledger.append(
                {
                    "period_end_at": period_end_at,
                    "users_len": len(normalized["users"]),
                    "models_len": len(normalized["models"]),
                    "users_scope": users_scope,
                    "models_scope": models_scope,
                    "hit_user_limit": len(normalized["users"]) >= limit,
                    "hit_model_limit": len(normalized["models"]) >= limit,
                    "zero_fill_applied": False,
                }
            )
        else:
            history_periods_by_position.append(None)
            history_users_by_position.append(None)
            history_models_by_position.append(None)
            history_user_scopes_by_position.append(None)
            history_model_scopes_by_position.append(None)
            missing = {"period_end_at": period_end_at, "reason": "deadline_skipped" if result.deadline_skipped else "request_failed"}
            missing_periods.append(missing)
            error = result.error or {"type": "unknown_error", "message": "request failed"}
            api_errors.append({"period_end_at": period_end_at, **redact_for_json(error)})
    baseline_periods = [period for period in history_periods_by_position[:baseline_workdays] if period is not None]
    trend_periods = [period for period in history_periods_by_position[:trend_workdays] if period is not None]
    baseline_successful = len(baseline_periods)
    baseline_avg_tokens = average_available(baseline_periods, "total_tokens")
    baseline_avg_cost = average_available(baseline_periods, "cost")
    baseline_avg_requests = average_available(baseline_periods, "requests")
    baseline_std_tokens = standard_deviation(baseline_periods, "total_tokens")
    latest_tokens = current["covered_totals"].get("total_tokens")
    latest_cost = current["covered_totals"].get("cost")
    latest_requests = current["covered_totals"].get("requests")
    coverage_scope = weakest_scope(
        [current["coverage_scope"]] + [period["scope"] for period in history_periods] if history_periods else [current["coverage_scope"]]
    )
    token_trend = {
        **metric_meta(coverage_scope),
        "latest_vs_avg_percent": percent_change(latest_tokens, baseline_avg_tokens),
        "latest_vs_first_percent": percent_change(latest_tokens, earliest_position_value(history_periods_by_position[:trend_workdays], "total_tokens")),
        "last_3_vs_first_3_percent": last_3_vs_first_3_by_position(history_periods_by_position[:trend_workdays], "total_tokens"),
        "slope_percent_per_workday": slope_percent_per_workday_by_position(
            history_periods_by_position[:trend_workdays],
            "total_tokens",
            trend_workdays,
        ),
        "volatility_cv": coefficient_of_variation_by_position(
            history_periods_by_position[:trend_workdays],
            "total_tokens",
            trend_workdays,
        ),
        "windows": {
            "latest_vs_avg_percent": "current_vs_baseline",
            "latest_vs_first_percent": "history_plus_current",
            "last_3_vs_first_3_percent": "history_only",
            "slope_percent_per_workday": "history_only",
            "volatility_cv": "history_only",
        },
    }
    cost_available = has_cost_available(
        current["covered_totals"],
        history_periods_by_position[:baseline_workdays],
        baseline_avg_cost,
    )
    if cost_available:
        cost_trend = {
            **metric_meta(coverage_scope),
            "cost_unit": "original",
            "latest_vs_avg_percent": percent_change(latest_cost, baseline_avg_cost),
            "latest_vs_first_percent": percent_change(
                latest_cost,
                earliest_position_value(history_periods_by_position[:trend_workdays], "cost"),
            ),
            "windows": {
                "latest_vs_avg_percent": "current_vs_baseline",
                "latest_vs_first_percent": "history_plus_current",
            },
        }
        cost_efficiency = {
            **metric_meta(coverage_scope),
            "cost_per_million_tokens": current["covered_totals"].get("cost_per_million_tokens"),
            "cost_per_request": current["covered_totals"].get("cost_per_request"),
            "token_cost_divergence": (
                numeric_or_none(cost_trend["latest_vs_avg_percent"]) - numeric_or_none(token_trend["latest_vs_avg_percent"])
                if numeric_or_none(cost_trend["latest_vs_avg_percent"]) is not None
                and numeric_or_none(token_trend["latest_vs_avg_percent"]) is not None
                else UNAVAILABLE
            ),
        }
    else:
        cost_trend = suppressed_cost_block(coverage_scope)
        cost_efficiency = suppressed_cost_block(coverage_scope)
    request_trend = {
        **metric_meta(coverage_scope),
        "latest_vs_avg_percent": percent_change(latest_requests, baseline_avg_requests),
        "latest_vs_first_percent": percent_change(
            latest_requests,
            earliest_position_value(history_periods_by_position[:trend_workdays], "requests"),
        ),
        "windows": {
            "latest_vs_avg_percent": "current_vs_baseline",
            "latest_vs_first_percent": "history_plus_current",
        },
    }
    classification = classify_token_trend(
        token_trend["latest_vs_avg_percent"],
        token_trend["last_3_vs_first_3_percent"],
        token_trend["volatility_cv"],
        token_trend["slope_percent_per_workday"],
        latest_tokens,
        baseline_avg_tokens,
        baseline_std_tokens,
        coverage_scope,
    )
    coverage_warning = coverage_scope != SCOPE_WITHIN_ENDPOINT_LIMIT
    status = "partial" if missing_periods or deadline_skipped else "complete"
    current_period = {
        "period_end_at": isoformat_seconds(end_at),
        "scope": current["coverage_scope"],
        "confidence": confidence_for_scope(current["coverage_scope"]),
        "covered_totals": current["covered_totals"],
        "current": True,
    }
    user_insights = user_insights_block(
        normalized_current["users"],
        history_users_by_position,
        current["covered_totals"],
        current["users_scope"],
        history_user_scopes_by_position,
        trend_workdays,
        top_n,
    )
    identity_warning = user_insights.pop("identity_warning")
    model_insights = model_insights_block(
        normalized_current["models"],
        history_models_by_position,
        current["models_scope"],
        history_model_scopes_by_position,
        trend_workdays,
        top_n,
    )
    token_structure_block = {**metric_meta(current["covered_totals"]["scope"]), **token_structure(current["covered_totals"], normalized_current["users"])}
    if token_structure_block.get("token_component_warning"):
        token_structure_block["confidence"] = "low"
    if not cost_efficiency.get("suppressed"):
        cost_efficiency["model_cost_metrics_suppressed"] = model_insights["cost_metrics_suppressed"]
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "thinking": thinking,
        "report_context": {
            "title": "AI 使用量日报",
            "generated_at": isoformat_seconds(dt.datetime.now(SHANGHAI_TZ)),
            "timezone": "Asia/Shanghai",
            "window": {
                "start_at": isoformat_seconds(window_start(end_at, hours)),
                "end_at": isoformat_seconds(end_at),
                "hours": hours,
            },
            "baseline": {
                "type": "script_recent_workday_average",
                "workdays_requested": baseline_workdays,
                "workdays_successful": baseline_successful,
                "workdays_source": "script_history_windows",
                "calendar": "standard_weekdays",
            },
            "window_policy": "weekday_end_date_rolling_window",
            "backend_window_alignment": "verified",
            "trend_workdays": trend_workdays,
        },
        "current": current,
        "baseline_comparison": {
            "users": [],
            "models": [],
            "company": {
                "total_tokens": {
                    "current": latest_tokens,
                    "average": baseline_avg_tokens,
                    "latest_vs_avg_percent": token_trend["latest_vs_avg_percent"],
                    "scope": coverage_scope,
                    "confidence": confidence_for_scope(coverage_scope),
                },
                "cost": {
                    "current": latest_cost,
                    "average": baseline_avg_cost,
                    "latest_vs_avg_percent": cost_trend.get("latest_vs_avg_percent", UNAVAILABLE),
                    "scope": coverage_scope,
                    "confidence": confidence_for_scope(coverage_scope),
                    "suppressed": not cost_available,
                },
                "requests": {
                    "current": latest_requests,
                    "average": baseline_avg_requests,
                    "latest_vs_avg_percent": request_trend["latest_vs_avg_percent"],
                    "scope": coverage_scope,
                    "confidence": confidence_for_scope(coverage_scope),
                },
            },
        },
        "company_trend": {
            "scope": coverage_scope,
            "confidence": confidence_for_scope(coverage_scope),
            "coverage_ledger": coverage_ledger,
            "history_periods": trend_periods,
            "history_plus_current": trend_periods + [current_period],
            "token_trend": token_trend,
            "cost_trend": cost_trend,
            "request_trend": request_trend,
            "classification": classification,
        },
        "user_insights": {
            **user_insights,
        },
        "model_insights": model_insights,
        "token_structure": token_structure_block,
        "cost_efficiency": cost_efficiency,
        "candidate_highlights": candidate_highlights(current["covered_totals"], baseline_avg_tokens, coverage_scope)[:8],
        "output_budget": {
            "max_stdout_bytes": max_stdout_bytes,
            "debug_full_available": False,
            "truncation": {"truncated": False, "collections": []},
        },
        "data_quality": {
            "missing_periods": missing_periods,
            "coverage_warning": coverage_warning,
            "coverage_scope": coverage_scope,
            "baseline_workdays_requested": baseline_workdays,
            "baseline_workdays_successful": baseline_successful,
            "baseline_workdays_source": "script_history_windows",
            "window_policy": "weekday_end_date_rolling_window",
            "backend_window_alignment": "verified",
            "identity_warning": identity_warning,
            "token_component_warning": bool(token_structure_block.get("token_component_warning")),
            "deadline_skipped": deadline_skipped,
            "api_errors": api_errors,
        },
        "llm_guidance": {
            "must_mention": [
                item
                for item, present in (
                    ("coverage_warning", coverage_warning),
                    ("identity_warning", identity_warning),
                    ("token_component_warning", bool(token_structure_block.get("token_component_warning"))),
                )
                if present
            ],
            "may_infer": [],
            "must_not_claim": [
                "Do not describe ranked_rows_only or limit_boundary_unknown data as whole-company truth.",
                "Do not infer missing/null numeric values as zero.",
                "Do not mention raw key names, key IDs, source fields, endpoint secrets, or auth material.",
            ],
        },
    }
    return redact_for_json(report)


def ensure_history_quality(
    history_results: list[tuple[dt.datetime, FetchResult]],
    trend_workdays: int,
    baseline_workdays: int,
) -> tuple[int, bool]:
    trend_successful = sum(1 for _, result in history_results[:trend_workdays] if result.ok)
    baseline_successful = sum(1 for _, result in history_results[:baseline_workdays] if result.ok)
    deadline_skipped = any(result.deadline_skipped for _, result in history_results)
    minimum_trend = max(3, math.ceil(trend_workdays * 0.7))
    minimum_baseline = max(3, math.ceil(baseline_workdays * 0.7))
    if trend_successful < minimum_trend:
        raise FatalError(
            f"trend history success count {trend_successful} is below required threshold {minimum_trend}"
        )
    if baseline_successful < minimum_baseline:
        raise FatalError(
            f"baseline history success count {baseline_successful} is below required threshold {minimum_baseline}"
        )
    return min(trend_successful, baseline_successful), deadline_skipped


def validate_debug_path(path: str) -> None:
    if not os.path.isabs(path):
        raise FatalError("--debug-full-output must be an absolute path")
    if os.path.isdir(path):
        raise FatalError("--debug-full-output must be a file path, not a directory")
    parent = os.path.dirname(path)
    if not parent or not os.path.isdir(parent):
        raise FatalError("--debug-full-output parent directory must exist")
    parent_stat = os.stat(parent)
    mode = parent_stat.st_mode
    uid = os.getuid()
    user_owned_0700 = parent_stat.st_uid == uid and stat.S_IMODE(mode) == 0o700
    sticky_temp = bool(mode & stat.S_ISVTX) and bool(mode & stat.S_IWOTH)
    if not (user_owned_0700 or sticky_temp):
        raise FatalError("--debug-full-output parent must be user-owned 0700 or a sticky temp directory")


def write_debug_file(path: str, payload: dict[str, Any]) -> None:
    validate_debug_path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(redact_for_json(payload), handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    os.chmod(path, 0o600)


def collect(args: argparse.Namespace, requester: Callable[[str, float, str | None], dict[str, Any]] = request_json) -> dict[str, Any]:
    end_at = parse_iso_datetime(args.end_at)
    hours = positive_float("--hours", args.hours)
    baseline_workdays = positive_int_range("--baseline-workdays", args.baseline_workdays, 1, 30)
    trend_workdays = positive_int_range("--trend-workdays", args.trend_workdays, 1, 30)
    limit = positive_int_range("--limit", args.limit, 1, 100)
    top_n = positive_int_range("--top-n", args.top_n, 1, limit)
    timeout = positive_float("--timeout", args.timeout)
    retries = positive_int_range("--retries", args.retries, 0, 5)
    deadline = positive_float("--deadline", args.deadline)
    minimum_request_seconds = positive_float("--minimum-request-seconds", args.minimum_request_seconds)
    endpoint = resolve_endpoint(args)
    token = os.environ.get(args.token_env_var)
    deadline_at = time.monotonic() + deadline
    base_params = {"hours": str(hours).rstrip("0").rstrip("."), "limit": str(limit)}
    current_params = {"end_at": isoformat_seconds(end_at), **base_params}
    current_result = fetch_with_retries(
        endpoint,
        current_params,
        timeout,
        retries,
        deadline_at,
        minimum_request_seconds,
        token,
        allow_deadline_skip=False,
        requester=requester,
    )
    if not current_result.ok or current_result.data is None:
        error = current_result.error or {"message": "current request failed"}
        raise FatalError(f"current request failed: {redact_text(json.dumps(error, ensure_ascii=False))}")
    collection_workdays = max(baseline_workdays, trend_workdays)
    history_windows = generate_history_windows(end_at, collection_workdays)
    history_results: list[tuple[dt.datetime, FetchResult]] = []
    for history_end_at in history_windows:
        result = fetch_with_retries(
            endpoint,
            {"end_at": isoformat_seconds(history_end_at), **base_params},
            timeout,
            retries,
            deadline_at,
            minimum_request_seconds,
            token,
            allow_deadline_skip=True,
            requester=requester,
        )
        history_results.append((history_end_at, result))
    _, deadline_skipped = ensure_history_quality(history_results, trend_workdays, baseline_workdays)
    report = build_report(
        end_at=end_at,
        hours=hours,
        baseline_workdays=baseline_workdays,
        trend_workdays=trend_workdays,
        limit=limit,
        top_n=top_n,
        current_response=current_result.data,
        history_results=history_results,
        deadline_skipped=deadline_skipped,
    )
    if args.debug_full_output:
        debug_payload = {
            "schema_version": SCHEMA_VERSION,
            "report": report,
            "request_summary": {
                "endpoint_host": urllib.parse.urlsplit(endpoint).netloc,
                "endpoint_path": urllib.parse.urlsplit(endpoint).path,
                "current_end_at": isoformat_seconds(end_at),
                "history_end_ats": [isoformat_seconds(value) for value in history_windows],
                "serial_requests": True,
            },
        }
        write_debug_file(args.debug_full_output, debug_payload)
        report["output_budget"]["debug_full_available"] = True
        report["output_budget"]["debug_full_basename"] = os.path.basename(args.debug_full_output)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        report = collect(args)
        sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        sys.stdout.write("\n")
        return 0
    except FatalError as exc:
        sys.stderr.write(f"fatal: {redact_text(str(exc))}\n")
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("fatal: interrupted\n")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
