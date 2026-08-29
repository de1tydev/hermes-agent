#!/usr/bin/env python3
"""Fetch TODE Status Hub timeline, render a scoped SVG/PNG, and output analysis JSON."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://status.tode.ltd"
DEFAULT_OUTPUT_DIR = "/tmp/hermes-service-status"
SCOPES = {
    "all": None,
    "company": None,
    "internal": {"internal-infra", "service-docker"},
    "public": {"external-public", "tode20-public"},
}


def parse_ts(value: str) -> dt.datetime:
    value = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iso_z(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_scope(text: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    t = text.lower()
    if any(k in t for k in ["内网", "内部", "基础设施", "internal", "infra"]):
        return "internal"
    if any(k in t for k in ["公网", "外网", "对外", "public", "external"]):
        return "public"
    if any(k in t for k in ["公司", "company"]):
        return "company"
    return "all"


def parse_hours(text: str, explicit_hours: float | None, explicit_range: str | None) -> float:
    if explicit_hours:
        return float(explicit_hours)
    if explicit_range:
        return {"24h": 24, "7d": 24 * 7, "30d": 24 * 30, "90d": 24 * 90}[explicit_range]

    t = text.lower().replace(" ", "")
    m = re.search(r"最近(\d+(?:\.\d+)?)(?:个)?(?:小时|小時|h|hour|hours)", t)
    if m:
        return float(m.group(1))
    m = re.search(r"最近(\d+(?:\.\d+)?)(?:天|日|d|day|days)", t)
    if m:
        return float(m.group(1)) * 24
    m = re.search(r"\b(24h|7d|30d|90d)\b", t)
    if m:
        return {"24h": 24, "7d": 24 * 7, "30d": 24 * 30, "90d": 24 * 90}[m.group(1)]
    if "一周" in t or "一星期" in t:
        return 24 * 7
    if "一个月" in t or "一月" in t:
        return 24 * 30
    if "三个月" in t or "90天" in t:
        return 24 * 90
    return 24


def fetch_range_for_hours(hours: float) -> str:
    if hours <= 24:
        return "24h"
    if hours <= 24 * 7:
        return "7d"
    if hours <= 24 * 30:
        return "30d"
    return "90d"


def fetch_timeline(base_url: str, range_name: str) -> dict[str, Any]:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", "api/timeline") + "?" + urllib.parse.urlencode({"range": range_name})
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-service-status-skill/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def filter_targets(payload: dict[str, Any], scope: str, start_at: dt.datetime, end_at: dt.datetime) -> list[dict[str, Any]]:
    groups = SCOPES[scope]
    result: list[dict[str, Any]] = []
    for target in payload.get("targets", []):
        if groups is not None and target.get("group") not in groups:
            continue
        buckets = []
        for b in target.get("buckets", []):
            bs = parse_ts(b["start"])
            be = parse_ts(b["end"])
            if be <= start_at or bs >= end_at:
                continue
            nb = dict(b)
            nb["_start_dt"] = bs
            nb["_end_dt"] = be
            buckets.append(nb)
        nt = dict(target)
        nt["buckets"] = buckets
        nt["uptime_percent"] = target_uptime(buckets)
        nt["latest_status"] = buckets[-1].get("status", "unknown") if buckets else "unknown"
        result.append(nt)
    return result


def target_uptime(buckets: list[dict[str, Any]]) -> float:
    up = down = unknown = 0
    for b in buckets:
        up += int(b.get("up_count") or 0)
        down += int(b.get("down_count") or 0)
        unknown += int(b.get("unknown_count") or 0)
    denom = up + down + unknown
    if denom <= 0:
        return 0.0
    return round(up / denom * 100, 2)


def status_color(status: str) -> str:
    return {
        "up": "#10b981",
        "down": "#ef4444",
        "maintenance": "#3b82f6",
        "unknown": "#f59e0b",
    }.get(status or "unknown", "#f59e0b")


def trunc(text: str, max_len: int) -> str:
    text = text or ""
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def fmt_local(ts: dt.datetime, fmt: str = "%m-%d %H:%M") -> str:
    return ts.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime(fmt)


def group_display_label(group: str | None) -> str:
    raw = group or ""
    key = raw.strip().lower()
    if key in {"external-public", "external services", "external service", "公网独立服务"}:
        return "公网独立服务"
    if key in {"tode20-public", "public domains", "公司内网对公网提供的服务"}:
        return "公司内网对公网提供的服务"
    if key in {"core", "internal-infra", "internal services", "internal service", "公司内部基础设施服务"}:
        return "公司内部基础设施服务"
    if key in {"docker workloads", "docker_workload", "service-docker", "docker-service", "公司内网的服务性 docker"}:
        return "公司内网的服务性 Docker"
    if key in {"ungrouped", ""}:
        return "未分组"
    return raw


def render_svg(targets: list[dict[str, Any]], scope: str, hours: float, start_at: dt.datetime, end_at: dt.datetime) -> str:
    width = 900
    label_w = 220
    upt_w = 72
    bar_x = label_w + 4
    bar_w = width - label_w - upt_w - 8
    row_h = 34
    grp_h = 28
    hdr_h = 72
    leg_h = 40
    label_gap = 10
    pad_x = 16
    pad_y = 12

    scope_title = {"all": "全部服务", "company": "公司服务", "internal": "内网服务", "public": "公网服务"}[scope]
    title = f"TODE {scope_title}最近 {format_hours(hours)} 运行状态"

    grouped: dict[str, list[dict[str, Any]]] = {}
    for t in targets:
        grouped.setdefault(t.get("group") or "ungrouped", []).append(t)
    groups = sorted(grouped)
    height = hdr_h + leg_h + label_gap + len(groups) * grp_h + len(targets) * row_h + pad_y
    parts: list[str] = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">')
    parts.append('<defs><filter id="cs" x="-2%" y="-5%" width="104%" height="120%"><feDropShadow dx="0" dy="1" stdDeviation="1.5" flood-color="#94a3b8" flood-opacity="0.18"/></filter></defs>')
    parts.append(f'<rect width="{width}" height="{height}" fill="#f8fafc"/>')
    parts.append(f'<text x="{pad_x}" y="34" font-family="system-ui,-apple-system,sans-serif" font-size="18" font-weight="bold" fill="#1e293b">{html.escape(title)}</text>')
    generated = dt.datetime.now(dt.timezone.utc)
    sub = f"生成时间：{fmt_local(generated, '%Y-%m-%d %H:%M:%S')} CST · 窗口：{fmt_local(start_at)} 至 {fmt_local(end_at)}"
    parts.append(f'<text x="{pad_x}" y="58" font-family="system-ui,-apple-system,sans-serif" font-size="12" fill="#64748b">{html.escape(sub)}</text>')
    legend_y = hdr_h + 6
    lx = pad_x
    for color, label in [("#10b981", "正常"), ("#ef4444", "异常"), ("#3b82f6", "维护"), ("#f59e0b", "未知")]:
        parts.append(f'<rect x="{lx}" y="{legend_y}" width="12" height="12" rx="2" fill="{color}"/>')
        parts.append(f'<text x="{lx+16}" y="{legend_y+10}" font-family="system-ui,-apple-system,sans-serif" font-size="11" fill="#475569">{html.escape(label)}</text>')
        lx += 100

    content_y = hdr_h + leg_h
    label_y = content_y - 4
    parts.append(f'<text id="timeline-start-label" x="{bar_x}" y="{label_y}" font-family="system-ui,-apple-system,sans-serif" font-size="11" fill="#64748b">{html.escape(fmt_local(start_at))}</text>')
    parts.append(f'<text id="timeline-end-label" x="{bar_x+bar_w}" y="{label_y}" text-anchor="end" font-family="system-ui,-apple-system,sans-serif" font-size="11" fill="#64748b">{html.escape(fmt_local(end_at))}</text>')
    marker_y1 = label_y + 3
    marker_y2 = content_y + label_gap
    parts.append(f'<line id="timeline-start-marker" x1="{bar_x}" y1="{marker_y1}" x2="{bar_x}" y2="{marker_y2}" stroke="#94a3b8" stroke-width="1" stroke-opacity="0.55"/>')
    parts.append(f'<line id="timeline-end-marker" x1="{bar_x+bar_w}" y1="{marker_y1}" x2="{bar_x+bar_w}" y2="{marker_y2}" stroke="#94a3b8" stroke-width="1" stroke-opacity="0.55"/>')

    y = content_y + label_gap
    for group in groups:
        rows = grouped[group]
        parts.append(f'<rect x="0" y="{y}" width="{width}" height="{grp_h}" fill="#e2e8f0"/>')
        parts.append(f'<text x="{pad_x}" y="{y+18}" font-family="system-ui,-apple-system,sans-serif" font-size="12" font-weight="600" fill="#334155">{html.escape(group_display_label(group) + " · " + str(len(rows)) + " 个目标")}</text>')
        y += grp_h
        for t in rows:
            parts.append(f'<rect x="{pad_x//2}" y="{y+1}" width="{width-pad_x}" height="{row_h-2}" rx="4" fill="#ffffff" filter="url(#cs)"/>')
            name_y = y + 14
            sub_y = y + 26
            parts.append(f'<text x="{pad_x}" y="{name_y}" font-family="system-ui,-apple-system,sans-serif" font-size="12" font-weight="600" fill="#1e293b">{html.escape(trunc(t.get("display_name") or t.get("target_id") or "unnamed", 26))}</text>')
            sub_label = " · ".join(x for x in [group_display_label(t.get("group")), t.get("endpoint")] if x)
            parts.append(f'<text x="{pad_x}" y="{sub_y}" font-family="system-ui,-apple-system,sans-serif" font-size="10" fill="#64748b">{html.escape(trunc(sub_label, 44))}</text>')
            buckets = t.get("buckets") or []
            n = max(1, len(buckets))
            bw = bar_w / n
            bx = float(bar_x)
            bar_h = 18.0
            bar_y = y + (row_h - bar_h) / 2
            if not buckets:
                parts.append(f'<rect x="{bx:.2f}" y="{bar_y:.2f}" width="{bar_w:.2f}" height="{bar_h:.2f}" rx="1" fill="#f59e0b"/>')
            for b in buckets:
                parts.append(f'<rect x="{bx:.2f}" y="{bar_y:.2f}" width="{max(0.5, bw-0.5):.2f}" height="{bar_h:.2f}" rx="1" fill="{status_color(b.get("status"))}"/>')
                bx += bw
            uptime = float(t.get("uptime_percent") or 0.0)
            parts.append(f'<text x="{width-pad_x}" y="{name_y}" font-family="system-ui,-apple-system,sans-serif" font-size="11" fill="#475569" text-anchor="end">{uptime:.2f}%</text>')
            y += row_h
    parts.append("</svg>")
    return "".join(parts)


def format_hours(hours: float) -> str:
    if abs(hours - 24) < 0.01:
        return "24小时"
    if hours % 24 == 0:
        return f"{int(hours//24)}天"
    if hours == int(hours):
        return f"{int(hours)}小时"
    return f"{hours:g}小时"


def analyze(targets: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for t in targets:
        buckets = t.get("buckets") or []
        down_buckets = sum(1 for b in buckets if b.get("status") == "down")
        unknown_buckets = sum(1 for b in buckets if b.get("status") == "unknown")
        maint_buckets = sum(1 for b in buckets if b.get("status") == "maintenance")
        failures: dict[str, int] = {}
        for b in buckets:
            for f in b.get("failures") or []:
                label = f.get("label") or f.get("reason") or "unknown"
                failures[label] = failures.get(label, 0) + int(f.get("count") or 1)
        rows.append({
            "target_id": t.get("target_id"),
            "display_name": t.get("display_name"),
            "group": t.get("group"),
            "endpoint": t.get("endpoint"),
            "latest_status": t.get("latest_status"),
            "uptime_percent": round(float(t.get("uptime_percent") or 0.0), 2),
            "down_buckets": down_buckets,
            "unknown_buckets": unknown_buckets,
            "maintenance_buckets": maint_buckets,
            "top_failure": max(failures.items(), key=lambda kv: kv[1])[0] if failures else None,
        })
    rows.sort(key=lambda r: (r["uptime_percent"], -r["down_buckets"], r.get("display_name") or ""))
    latest_bad = [r for r in rows if r["latest_status"] != "up"]
    ever_bad = [r for r in rows if r["down_buckets"] > 0 or r["unknown_buckets"] > 0]
    return {
        "total_targets": len(rows),
        "latest_bad_count": len(latest_bad),
        "window_bad_count": len(ever_bad),
        "maintenance_target_count": sum(1 for r in rows if r["maintenance_buckets"] > 0),
        "worst_targets": rows[:5],
        "latest_bad_targets": latest_bad[:10],
    }


def convert_png(svg_path: Path, png_path: Path) -> str | None:
    cmd = ["rsvg-convert", "-f", "png", "-o", str(png_path), str(svg_path)]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except Exception as exc:
        return str(exc)
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default="", help="Original user request for simple scope/time parsing")
    parser.add_argument("--scope", choices=sorted(SCOPES), default=None)
    parser.add_argument("--hours", type=float, default=None)
    parser.add_argument("--range", choices=["24h", "7d", "30d", "90d"], default=None)
    parser.add_argument("--base-url", default=os.environ.get("STATUS_HUB_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument(
        "--output-dir",
        default=os.environ.get(
            "HERMES_SERVICE_STATUS_OUTPUT_DIR",
            DEFAULT_OUTPUT_DIR,
        ),
    )
    args = parser.parse_args()

    scope = parse_scope(args.text, args.scope)
    hours = min(max(parse_hours(args.text, args.hours, args.range), 1), 24 * 90)
    range_name = fetch_range_for_hours(hours)
    payload = fetch_timeline(args.base_url, range_name)
    generated = parse_ts(payload.get("generated_at") or dt.datetime.now(dt.timezone.utc).isoformat())
    end_at = min(generated, dt.datetime.now(dt.timezone.utc))
    start_at = end_at - dt.timedelta(hours=hours)
    targets = filter_targets(payload, scope, start_at, end_at)
    svg = render_svg(targets, scope, hours, start_at, end_at)

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_scope = re.sub(r"[^a-z0-9_-]+", "-", scope.lower())
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    svg_path = out_dir / f"hermes-service-status-{safe_scope}-{stamp}.svg"
    png_path = out_dir / f"hermes-service-status-{safe_scope}-{stamp}.png"
    svg_path.write_text(svg, encoding="utf-8")
    png_error = convert_png(svg_path, png_path)

    analysis = analyze(targets)
    result = {
        "schema_version": "service_status_timeline.v1",
        "source": {
            "base_url": args.base_url,
            "api_range": range_name,
        },
        "request": {
            "text": args.text,
            "scope": scope,
            "hours": hours,
        },
        "window": {
            "start_at": iso_z(start_at),
            "end_at": iso_z(end_at),
            "start_display": fmt_local(start_at),
            "end_display": fmt_local(end_at),
        },
        "media": {
            "svg_path": str(svg_path),
            "png_path": str(png_path) if png_path.exists() and png_error is None else None,
            "png_error": png_error,
        },
        "analysis": analysis,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
