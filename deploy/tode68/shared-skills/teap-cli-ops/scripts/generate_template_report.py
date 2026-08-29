#!/usr/bin/env python3
"""Generate a TEAP Template-A report without replacing the template layout.

The script has two responsibilities:

1. collect repeatable TEAP result data through the ``teap`` CLI;
2. render a content plan into the existing Template-A docx, preserving the
   template's paragraph/table formatting and inserting generated figures.

For production report writing, agents should inspect ``report_context.json`` and
provide ``--content-json`` with richer case-specific text. If no content JSON is
given, the script creates a conservative data-driven draft so the workflow is
testable end to end.
"""

from __future__ import annotations

import argparse
import calendar
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from PIL import Image


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
XML_NS = "http://www.w3.org/XML/1998/namespace"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
W15_NS = "http://schemas.microsoft.com/office/word/2012/wordml"
WP14_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"

NS = {
    "w": W_NS,
    "r": R_NS,
    "rel": REL_NS,
    "ct": CT_NS,
    "wp": WP_NS,
    "a": A_NS,
    "pic": PIC_NS,
    "mc": MC_NS,
    "w14": W14_NS,
    "w15": W15_NS,
    "wp14": WP14_NS,
}

for prefix, uri in {
    "w": W_NS,
    "r": R_NS,
    "wp": WP_NS,
    "a": A_NS,
    "pic": PIC_NS,
    "mc": MC_NS,
    "w14": W14_NS,
    "w15": W15_NS,
    "wp14": WP14_NS,
}.items():
    ET.register_namespace(prefix, uri)

EMU_PER_PX = 9525
REPORT_TABLE_COUNT = 15
REPORT_CHART_CJK_FONT_PATH_ENV = "TEAP_REPORT_CJK_FONT_PATH"
REPORT_CHART_LATIN_FONT_PATH_ENV = "TEAP_REPORT_LATIN_FONT_PATH"
REPORT_FONT_DIR_ENV = "TEAP_REPORT_FONT_DIR"
REPORT_FONT_AUTO_INSTALL_ENV = "TEAP_REPORT_AUTO_INSTALL_FONTS"
REPORT_FONT_APT_PACKAGES = ("fontconfig", "fonts-noto-cjk", "fonts-liberation2")
ANALYSIS_PARAGRAPH_INDICES = {
    6,
    14,
    16,
    24,
    39,
    42,
    53,
    57,
    58,
    59,
    66,
    67,
    68,
    69,
    75,
    76,
    77,
    78,
    83,
    84,
    85,
    86,
    87,
}
AGENT_PARAGRAPH_INDICES = ANALYSIS_PARAGRAPH_INDICES - {83, 84, 85, 86, 87}

GEN_TYPES = [
    ("煤电", "coal", "基荷/调节电源"),
    ("气电", "gas", "调峰电源"),
    ("核电", "nuclear", "基荷电源"),
    ("生物质", "biomass", "基荷电源"),
    ("三段式水电", "hydro", "调节电源"),
    ("库容式水电", "hydro_reservoir", "调节电源"),
    ("风电", "wind", "主力电源"),
    ("光伏", "solar", "主力电源"),
    ("光热", "csp", "可再生电源"),
    ("储能", "storage", "灵活性资源"),
]

EMOJI_RANGES = [
    (0x1F1E6, 0x1F1FF),  # regional indicator symbols
    (0x1F300, 0x1FAFF),  # pictographs, symbols, supplemental symbols
    (0x2600, 0x26FF),  # misc symbols
    (0x2700, 0x27BF),  # dingbats
    (0x20E3, 0x20E3),  # combining enclosing keycap
    (0x200D, 0x200D),  # zero width joiner
    (0xFE0E, 0xFE0F),  # variation selectors
    (0xE0020, 0xE007F),  # tag characters used in emoji sequences
]


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    skill_dir = script_dir.parent
    template = args.template or skill_dir / "template" / "模板A_规划类仿真分析报告_公文版.docx"
    output = args.output or Path.cwd() / f"teap_result_{args.task_id}_template_A_report.docx"
    work_dir = args.work_dir or Path(tempfile.mkdtemp(prefix=f"teap_template_a_{args.task_id}_"))

    if not template.exists():
        raise SystemExit(f"Template not found: {template}")
    if not shutil.which(args.teap_bin):
        raise SystemExit(f"teap executable not found: {args.teap_bin}")
    output.parent.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    if args.server:
        run_teap(args.teap_bin, ["config", "set-server", args.server], work_dir)

    raw = collect_data(args.teap_bin, args.task_id, work_dir, include_hourly=not args.no_hourly, reuse_data=args.reuse_data)
    facts = build_facts(raw)
    figures = build_figures(facts, work_dir / "figures")
    context_path = work_dir / "report_context.json"
    context_path.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.collect_only:
        print_json(
            {
                "task_id": args.task_id,
                "context": str(context_path),
                "work_dir": str(work_dir),
                "figures": {key: str(value) for key, value in figures.items()},
                "next_step": "Ask an agent/model to write content JSON, then rerun with --content-json.",
            }
        )
        return 0

    content = build_render_content(facts, args.content_json, allow_content_tables=args.allow_content_tables)
    render_docx(template, output, content, figures)
    print_json(
        {
            "task_id": args.task_id,
            "output": str(output),
            "template": str(template),
            "context": str(context_path),
            "work_dir": str(work_dir),
            "figures": {key: str(value) for key, value in figures.items()},
            "content_source": str(args.content_json) if args.content_json else "built-in draft",
            "content_tables": "agent supplied" if args.allow_content_tables and args.content_json else "deterministic",
        }
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Template-A TEAP planning simulation report.")
    parser.add_argument("task_id", type=int, help="TEAP task/result id.")
    parser.add_argument("--server", help="TEAP server base URL.")
    parser.add_argument("--output", type=Path, help="Output .docx path.")
    parser.add_argument("--template", type=Path, help="Template .docx path.")
    parser.add_argument("--work-dir", type=Path, help="Work directory for data and figures.")
    parser.add_argument("--content-json", type=Path, help="Agent-authored content plan JSON.")
    parser.add_argument("--allow-content-tables", action="store_true", help="Allow --content-json to replace deterministic tables after schema validation.")
    parser.add_argument("--collect-only", action="store_true", help="Only collect data and generate report_context.json/figures.")
    parser.add_argument("--reuse-data", action="store_true", help="Reuse existing JSON/TR files in --work-dir instead of querying TEAP again.")
    parser.add_argument("--no-hourly", action="store_true", help="Skip 24 hourly typical-day CLI queries.")
    parser.add_argument("--teap-bin", default="teap", help="teap executable.")
    return parser.parse_args()


def collect_data(teap_bin: str, task_id: int, work_dir: Path, *, include_hourly: bool, reuse_data: bool) -> dict[str, Any]:
    commands: list[str] = []
    captures: dict[str, Any] = {"commands": commands}
    captures["task_status"] = run_json(teap_bin, ["task", "status", "--task-record-id", str(task_id)], work_dir, "task_status.json", commands, reuse=reuse_data)
    captures["parameter"] = run_json(teap_bin, ["result", "get", str(task_id), "-g", "parameter"], work_dir, "parameter.json", commands, reuse=reuse_data)
    captures["key_summaries"] = run_json(teap_bin, ["result", "get", str(task_id), "-g", "_result.key_summaries"], work_dir, "key_summaries.json", commands, reuse=reuse_data)
    captures["cost_and_penalty"] = run_json(teap_bin, ["result", "get", str(task_id), "-g", "_result.cost_and_penalty"], work_dir, "cost_and_penalty.json", commands, reuse=reuse_data)
    captures["neps_power"] = run_json(teap_bin, ["result", "get", str(task_id), "-g", "_result.balance_df.neps_power", "-l"], work_dir, "neps_power.json", commands, reuse=reuse_data)
    captures["neps_electricity_monthly"] = run_json(
        teap_bin,
        ["result", "get", str(task_id), "-g", "_result.balance_df.neps_electricity_monthly", "-l"],
        work_dir,
        "neps_electricity_monthly.json",
        commands,
        reuse=reuse_data,
    )
    tr_path = work_dir / f"result_{task_id}.tr"
    captures["download_tr"] = run_json(teap_bin, ["result", "download-tr", str(task_id), "--output", str(tr_path)], work_dir, "download_tr.json", commands, reuse=reuse_data)
    captures["common_info"] = extract_tr_json(tr_path, "_result.common_info")
    captures["topology"] = extract_topology(tr_path)

    if include_hourly:
        idx = typical_hour_index(captures["neps_power"].get("data", []))
        day_start = max(0, (idx // 24) * 24)
        hourly = []
        for hour_idx in range(day_start, day_start + 24):
            try:
                payload = run_json(
                    teap_bin,
                    ["result", "get", str(task_id), "-g", f"_result.balance_df.neps_alltime_power.{hour_idx}", "-l"],
                    work_dir,
                    f"hour_{hour_idx:04d}.json",
                    commands,
                    reuse=reuse_data,
                )
                hourly.append({"hour_index": hour_idx, "data": payload.get("data", [])})
            except SystemExit:
                break
        captures["typical_day"] = hourly
    else:
        captures["typical_day"] = []
    return captures


def run_teap(teap_bin: str, args: list[str], cwd: Path) -> None:
    cmd = [teap_bin, "--output", "json", *args]
    completed = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.stderr.strip() or completed.stdout.strip())


def run_json(teap_bin: str, args: list[str], cwd: Path, filename: str, commands: list[str], *, reuse: bool = False) -> dict[str, Any]:
    cmd = [teap_bin, "--output", "json", *args]
    commands.append(" ".join(cmd))
    target = cwd / filename
    if reuse and target.exists():
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Existing JSON file is invalid: {target}") from exc
    completed = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)
    target.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise SystemExit(f"teap command failed: {' '.join(cmd)}\n{message}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"teap command did not return JSON: {' '.join(cmd)}") from exc


def extract_tr_json(tr_path: Path, member_name: str) -> dict[str, Any]:
    try:
        with tarfile.open(tr_path, "r:gz") as archive:
            data = read_tar_member(archive, member_name)
            return json.loads(data.decode("utf-8")) if data is not None else {}
    except (OSError, tarfile.TarError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def read_tar_member(archive: tarfile.TarFile, name: str) -> bytes | None:
    try:
        member = archive.extractfile(name)
    except KeyError:
        return None
    return member.read() if member is not None else None


def extract_topology(tr_path: Path) -> dict[str, Any]:
    try:
        import pandas as pd
    except Exception:
        return {"nodes": [], "edges": [], "source": "pyarrow/pandas unavailable"}

    def read_feather_member(archive: tarfile.TarFile, name: str) -> Any | None:
        data = read_tar_member(archive, name)
        if data is None:
            return None
        handle = tempfile.NamedTemporaryFile(prefix=name.replace(".", "_"), suffix=".feather", delete=False)
        tmp = Path(handle.name)
        try:
            handle.write(data)
            handle.close()
            return pd.read_feather(tmp)
        finally:
            handle.close()
            tmp.unlink(missing_ok=True)

    try:
        with tarfile.open(tr_path, "r:gz") as archive:
            bus = read_feather_member(archive, "_input.bus")
            ac_line = read_feather_member(archive, "_input.ac_line")
            dc_line = read_feather_member(archive, "_input.dc_line")
    except (OSError, tarfile.TarError):
        return {"nodes": [], "edges": [], "source": "tr read failed"}

    if bus is None:
        return {"nodes": [], "edges": [], "source": "no _input.bus"}

    nodes = []
    for row in bus.to_dict("records"):
        nodes.append(
            {
                "index": as_int(row.get("index")),
                "name": str(row.get("name") or row.get("bpa_busname") or row.get("index")),
                "vn_kv": as_float(row.get("vn_kv")),
                "lon": as_float(row.get("lon"), default=float("nan")),
                "lat": as_float(row.get("lat"), default=float("nan")),
                "zone": row.get("zone"),
                "in_service": bool(row.get("in_service", True)),
            }
        )

    edges = []
    for df, kind in [(ac_line, "AC"), (dc_line, "DC")]:
        if df is None:
            continue
        for row in df.to_dict("records"):
            edges.append(
                {
                    "name": str(row.get("name") or ""),
                    "from_bus": as_int(row.get("from_bus")),
                    "to_bus": as_int(row.get("to_bus")),
                    "vn_kv": as_float(row.get("vn_kv")),
                    "kind": kind,
                    "in_service": bool(row.get("in_service", True)),
                }
            )

    filtered_nodes, filtered_edges, filter_note = filter_topology(nodes, edges)
    return {
        "nodes": filtered_nodes,
        "edges": filtered_edges,
        "raw_node_count": len(nodes),
        "raw_edge_count": len(edges),
        "filter_note": filter_note,
        "source": "_input.bus/_input.ac_line/_input.dc_line",
    }


def filter_topology(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    active_nodes = [node for node in nodes if node.get("in_service", True)]
    active_edges = [edge for edge in edges if edge.get("in_service", True)]
    if len(active_nodes) <= 100:
        node_ids = {node["index"] for node in active_nodes}
        return active_nodes, [edge for edge in active_edges if edge["from_bus"] in node_ids and edge["to_bus"] in node_ids], "all in-service nodes"
    filtered = [node for node in active_nodes if as_float(node.get("vn_kv")) >= 500]
    node_ids = {node["index"] for node in filtered}
    filtered_edges = [
        edge
        for edge in active_edges
        if edge["from_bus"] in node_ids and edge["to_bus"] in node_ids and as_float(edge.get("vn_kv")) >= 500
    ]
    return filtered, filtered_edges, "filtered to >=500kV because node count exceeded 100"


def as_int(value: Any, default: int = -1) -> int:
    try:
        if value != value:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def build_facts(raw: dict[str, Any]) -> dict[str, Any]:
    task = raw["task_status"]
    params = raw["parameter"]["data"]
    case_info = params.get("case_info", {})
    key = raw["key_summaries"].get("data", {})
    power_rows = raw["neps_power"].get("data", [])
    energy_rows = raw["neps_electricity_monthly"].get("data", [])
    cost_rows = raw["cost_and_penalty"].get("cost_and_penalty_table", {}).get("data", [])
    typical_day = raw.get("typical_day", [])

    months = [name for name in (energy_rows[0] if energy_rows else {}) if re.fullmatch(r"\d{4}年\d+月", str(name))]
    region = infer_region(task.get("case_name") or case_info.get("case_name") or "")
    planning_year = infer_year(case_info.get("start_datetime"))
    gen_cap = key.get("gen_cap_total", {})
    gen_power = key.get("gen_power_total", {})
    gen_hours = key.get("gen_util_hour", {})
    renewable = key.get("renewable_electricity", {})
    renewable_rate = key.get("renewable_util_rate", {})

    storage_dec = row_number(power_rows, "10.新型储能", last_month(months)) * 10
    pump_dec = row_number(power_rows, "4.抽水蓄能", last_month(months)) * 10
    total_capacity = sum_number(gen_cap.values()) + storage_dec + pump_dec
    total_generation_mwh = sum_number(gen_power.values())
    max_month, max_gap_10mw = max_month_pair(power_rows, "十四、总电力缺口")

    capacity_rows = []
    for label, key_name, note in GEN_TYPES:
        if key_name == "storage":
            cap = storage_dec + pump_dec
            energy = row_number(energy_rows, "1.储能发电", "合计") * 100000 + row_number(energy_rows, "1.发电电量", "合计") * 100000
            hour = safe_div(energy, cap)
        elif key_name == "hydro_reservoir" or key_name == "csp":
            cap = 0.0
            energy = 0.0
            hour = 0.0
        else:
            cap = as_float(gen_cap.get(key_name))
            energy = as_float(gen_power.get(key_name))
            hour = as_float(gen_hours.get(key_name))
        capacity_rows.append({"type": label, "capacity_mw": cap, "share": safe_div(cap, total_capacity), "note": note, "energy_mwh": energy, "util_hour": hour})

    monthly = []
    for month in months:
        wind_curt_rate = row_number(energy_rows, "3.弃风率%", month)
        solar_curt_rate = row_number(energy_rows, "3.弃光率%", month)
        monthly.append(
            {
                "month": month,
                "load_energy_gwh": row_number(energy_rows, "1.负荷电量", month) * 100,
                "wind_gwh": row_number(energy_rows, "1.风电发电", month) * 100,
                "solar_gwh": row_number(energy_rows, "1.光伏发电", month) * 100,
                "coal_gwh": row_number(energy_rows, "八、煤电发电", month) * 100,
                "gas_gwh": row_number(energy_rows, "六、气电发电", month) * 100,
                "hydro_gwh": row_number(energy_rows, "1.水电发电", month) * 100,
                "wind_util_rate": normalize_util_rate(wind_curt_rate),
                "solar_util_rate": normalize_util_rate(solar_curt_rate),
                "shortage_gwh": abs(row_number(energy_rows, "十六、电量不足", month)) * 100,
                "load_gap_mw": abs(row_number(power_rows, "1.负荷缺口", month)) * 10,
                "reserve_up_gap_mw": abs(row_number(power_rows, "2.旋备缺口", month)) * 10,
                "reserve_emer_gap_mw": abs(row_number(power_rows, "3.停备缺口", month)) * 10,
                "max_load_mw": row_number(power_rows, "1.本地负荷", month) * 10,
            }
        )

    cost_total = row_number(cost_rows, "一、运行成本", "全系统") + row_number(cost_rows, "三、惩罚", "全系统")
    cost_items = [
        {"name": "存量煤电运行成本", "value_yuan": row_number(cost_rows, "1) 存量煤电运行", "全系统"), "nature": "正常运营成本"},
        {"name": "新能源建设/运行成本", "value_yuan": row_number(cost_rows, "8.风电建设", "全系统") + row_number(cost_rows, "9.光伏建设", "全系统"), "nature": "正常/惩罚性"},
        {"name": "储能建设/运行成本", "value_yuan": row_number(cost_rows, "10.抽蓄与储能", "全系统") + row_number(cost_rows, "11.电池建设", "全系统"), "nature": "正常/惩罚性"},
        {"name": "缺电惩罚成本", "value_yuan": row_number(cost_rows, "1) 负荷松弛惩罚", "全系统") + row_number(cost_rows, "6) 最大缺电功率惩罚", "全系统"), "nature": "惩罚性成本"},
        {"name": "备用松弛惩罚", "value_yuan": row_number(cost_rows, "3) 旋备松弛惩罚", "全系统") + row_number(cost_rows, "4) 停备松弛惩罚", "全系统"), "nature": "惩罚性成本"},
    ]

    flex_capacity = as_float(gen_cap.get("gas")) + as_float(gen_cap.get("hydro")) + storage_dec + pump_dec
    facts = {
        "task": task,
        "case_info": case_info,
        "region": region,
        "planning_year": planning_year,
        "analysis_date": date.today().isoformat(),
        "exec_seconds": as_float(raw.get("common_info", {}).get("job_exec_cost_time")),
        "zone_count": len(key.get("zone_summaries") or {}) or 1,
        "months": months,
        "capacity_rows": capacity_rows,
        "monthly": monthly,
        "cost_items": cost_items,
        "cost_total_yuan": cost_total,
        "renewable": renewable,
        "renewable_rate": renewable_rate,
        "total_capacity_mw": total_capacity,
        "total_generation_mwh": total_generation_mwh,
        "max_load_mw": max([m["max_load_mw"] for m in monthly] or [0]),
        "avg_load_mw": row_number(energy_rows, "1.负荷电量", "合计") * 100000 / 8760,
        "energy_shortage_mwh": row_number(energy_rows, "十六、电量不足", "合计") * 100000,
        "max_gap_month": max_month,
        "max_gap_mw": max_gap_10mw * 10,
        "load_gap_mw": max([m["load_gap_mw"] for m in monthly] or [0]),
        "reserve_up_gap_mw": max([m["reserve_up_gap_mw"] for m in monthly] or [0]),
        "reserve_emer_gap_mw": max([m["reserve_emer_gap_mw"] for m in monthly] or [0]),
        "renewable_capacity_share": safe_div(as_float(gen_cap.get("wind")) + as_float(gen_cap.get("solar")), total_capacity),
        "renewable_generation_share": safe_div(as_float(renewable.get("renewable_acc")), total_generation_mwh),
        "flex_capacity_mw": flex_capacity,
        "flex_capacity_share": safe_div(flex_capacity, total_capacity),
        "typical_day": build_typical_day(typical_day),
        "topology": raw.get("topology", {"nodes": [], "edges": []}),
    }
    facts["max_shortage_month"] = max(facts["monthly"], key=lambda item: item["load_gap_mw"] + item["reserve_up_gap_mw"] + item["reserve_emer_gap_mw"], default={})
    facts["agent_instructions"] = {
        "content_json_schema": {
            "paragraphs": "mapping of allowed template body paragraph index string to replacement text; do not replace title, captions, headings, tables, or section 6.3 sensitivity paragraphs",
            "tables": "mapping of template table number string (1-15) to rows, each row is a list of cell strings",
            "omit_tables": "optional list of table numbers that should remain template-shaped but say 不适用",
        },
        "writing_requirements": [
            "Preserve the template structure; do not add command appendices.",
            "Write the report prose as a case-specific analysis, not a numeric fill-in. Explain the mechanisms behind the results.",
            "Expand qualitative analysis based on the result facts, especially reserve gaps, renewable accommodation, costs, resource-structure issues, typical-day behavior, and recommended planning actions.",
            "For the required paragraph keys, prefer 120-220 Chinese characters per analytical paragraph when the template location can hold a paragraph-length explanation.",
            f"Allowed paragraph keys: {', '.join(str(index) for index in sorted(AGENT_PARAGRAPH_INDICES))}.",
            "When a figure is generated from aggregated rather than hourly data, say so in nearby prose.",
        ],
    }
    return facts


def draft_content(facts: dict[str, Any]) -> dict[str, Any]:
    region = facts["region"]
    year = facts["planning_year"]
    task = facts["task"]
    case = facts["case_info"]
    cap_rows = facts["capacity_rows"]
    monthly = facts["monthly"]
    ren = facts["renewable"]
    ren_rate = facts["renewable_rate"]
    max_month = facts.get("max_shortage_month") or {}
    penalty_yuan = sum(item["value_yuan"] for item in facts["cost_items"] if "惩罚" in item["name"])
    flex_text = "不足" if facts["reserve_up_gap_mw"] or facts["reserve_emer_gap_mw"] else "基本满足"
    ren_text = "良好" if as_float(ren_rate.get("renewable")) >= 0.98 else "存在压力"

    table1 = [
        ["项目", "内容"],
        ["任务编号", str(task.get("task_id", ""))],
        ["算例名称", task.get("case_name") or case.get("case_name", "")],
        ["任务类型", task.get("job_type_name", "")],
        ["任务状态", task.get("status_name") or task.get("status", "")],
        ["仿真时间范围", f"{case.get('start_datetime', '')} 至 {case.get('end_datetime', '')}"],
        ["计算耗时", f"{fmt(facts['exec_seconds'])} 秒"],
        ["分析区域", region],
        ["规划水平年", str(year)],
        ["分析日期", facts["analysis_date"]],
        ["编制方式", "TEAP 模板报告生成工作流"],
    ]
    table2 = [
        ["参数", "数值", "说明"],
        ["仿真起始时间", str(case.get("start_datetime", "")), "用于界定年度时序样本起点"],
        ["仿真结束时间", str(case.get("end_datetime", "")), "用于界定年度时序样本终点"],
        ["时间分辨率", f"小时级 ({case.get('data_freq', 'H')})", "用于电力平衡与典型日分析"],
        ["分区数量", str(facts["zone_count"]), "按结果汇总口径识别"],
        ["总装机容量", f"{fmt(facts['total_capacity_mw'])} MW", "含常规电源、新能源和灵活性资源"],
        ["最大负荷", f"{fmt(facts['max_load_mw'])} MW", "月度电力平衡表中识别"],
        ["平均负荷", f"{fmt(facts['avg_load_mw'])} MW", "由全年负荷电量折算"],
        ["拓扑数据", topology_summary(facts.get("topology", {})), "来自 .tr 中节点和线路输入表"],
    ]
    table3 = [["电源类型", "装机容量（MW）", "装机占比", "年发电量（GWh）", "利用小时（h）", "功能定位"]] + [
        [row["type"], fmt(row["capacity_mw"]), pct(row["share"]), fmt(row["energy_mwh"] / 1000), fmt(row["util_hour"]), row["note"]] for row in cap_rows
    ] + [["合计", fmt(facts["total_capacity_mw"]), "100.00%", fmt(facts["total_generation_mwh"] / 1000), "-", "-"]]
    table4 = [["电源类型", f"{region}分区装机（MW）", "全系统合计（MW）", "说明"]]
    for label in [row["type"] for row in cap_rows]:
        value = next((row["capacity_mw"] for row in cap_rows if row["type"] == label), 0.0)
        note = next((row["note"] for row in cap_rows if row["type"] == label), "-")
        table4.append([label, fmt(value), fmt(value), note])
    table5 = [
        ["指标", "数值", "评价"],
        ["全年总发电量", f"{fmt(facts['total_generation_mwh'] / 1000)} GWh", "满足电量平衡需求" if facts["energy_shortage_mwh"] == 0 else "存在电量缺口"],
        ["最大负荷", f"{fmt(facts['max_load_mw'])} MW", "校核可靠容量需求"],
        ["平均负荷", f"{fmt(facts['avg_load_mw'])} MW", "反映全年负荷水平"],
        ["全年电量不足", f"{fmt(facts['energy_shortage_mwh'])} MWh", "无硬缺电" if facts["energy_shortage_mwh"] == 0 else "存在电量风险"],
        ["最大电力缺口", f"{fmt(facts['max_gap_mw'])} MW", facts["max_gap_month"] or "未识别"],
        ["最大旋转备用缺口", f"{fmt(facts['reserve_up_gap_mw'])} MW", "备用不足" if facts["reserve_up_gap_mw"] else "充足"],
        ["最大停备/紧急备用缺口", f"{fmt(facts['reserve_emer_gap_mw'])} MW", "备用不足" if facts["reserve_emer_gap_mw"] else "充足"],
        ["灵活性资源占比", pct(facts["flex_capacity_share"]), "衡量调节能力基础"],
    ]
    table6 = [["电源类型", "年发电量（GWh）", "装机容量（MW）", "利用小时（h）", "运行评价"]] + [
        [row["type"], fmt(row["energy_mwh"] / 1000), fmt(row["capacity_mw"]), fmt(row["util_hour"]), generation_comment(row)] for row in cap_rows if row["energy_mwh"] or row["capacity_mw"]
    ]
    table7 = [["成本项目", "金额（亿元）", "占总成本比例", "性质", "说明"]]
    for item in facts["cost_items"]:
        table7.append([item["name"], fmt(item["value_yuan"] / 100000000), pct(safe_div(item["value_yuan"], facts["cost_total_yuan"])), item["nature"], cost_comment(item)])
    table7.append(["合计", fmt(facts["cost_total_yuan"] / 100000000), "100.00%", "-", "系统综合成本口径"])
    table8 = [
        ["指标", "风电", "光伏", "风光合计"],
        ["发电量 (GWh)", fmt(as_float(ren.get("wind_acc")) / 1000), fmt(as_float(ren.get("solar_acc")) / 1000), fmt(as_float(ren.get("renewable_acc")) / 1000)],
        ["弃电量 (GWh)", fmt(as_float(ren.get("wind_curt")) / 1000), fmt(as_float(ren.get("solar_curt")) / 1000), fmt(as_float(ren.get("renewable_curt")) / 1000)],
        ["利用率", pct(as_float(ren_rate.get("wind"))), pct(as_float(ren_rate.get("solar"))), pct(as_float(ren_rate.get("renewable")))],
        ["利用小时 (h)", fmt(cap_value(cap_rows, "风电", "util_hour")), fmt(cap_value(cap_rows, "光伏", "util_hour")), "-"],
        ["装机容量 (MW)", fmt(cap_value(cap_rows, "风电")), fmt(cap_value(cap_rows, "光伏")), fmt(cap_value(cap_rows, "风电") + cap_value(cap_rows, "光伏"))],
        ["装机占比", pct(safe_div(cap_value(cap_rows, "风电"), facts["total_capacity_mw"])), pct(safe_div(cap_value(cap_rows, "光伏"), facts["total_capacity_mw"])), pct(facts["renewable_capacity_share"])],
        ["消纳评价", renewable_comment(as_float(ren_rate.get("wind"))), renewable_comment(as_float(ren_rate.get("solar"))), renewable_comment(as_float(ren_rate.get("renewable")))],
    ]
    table9 = [["月份", "风电上网电量（GWh）", "光伏上网电量（GWh）", "风电利用率", "光伏利用率", "消纳评价"]] + [
        [short_month(m["month"]), fmt(m["wind_gwh"]), fmt(m["solar_gwh"]), pct(m["wind_util_rate"]), pct(m["solar_util_rate"]), monthly_renewable_comment(m)] for m in monthly
    ]
    table10 = [
        ["指标", "数值", "说明"],
        ["全年电量不足", f"{fmt(facts['energy_shortage_mwh'] / 100000)} 亿 kWh", "年度电量供需平衡校核"],
        ["最大电力缺口时刻", facts["max_gap_month"] or "-", "月度电力缺口最大值所在月份"],
        ["最大电力缺口", f"{fmt(facts['max_gap_mw'])} MW", "反映容量约束强度"],
        ["最大备用缺口", f"{fmt(max(facts['reserve_up_gap_mw'], facts['reserve_emer_gap_mw']))} MW", "取旋备和停备/紧急备用较大值"],
        ["主要风险月份", max_month.get("month", "-"), "综合负荷和备用缺口识别"],
        ["风险类型", risk_type(facts), "用于指向后续规划建议"],
    ]
    table11 = [["月份", "最大负荷（MW）", "负荷缺额（MW）", "旋转备用缺口（MW）", "停备/紧急备用缺口（MW）", "电量不足（GWh）", "严重程度"]]
    for m in monthly:
        gap = max(m["load_gap_mw"], m["reserve_up_gap_mw"], m["reserve_emer_gap_mw"])
        table11.append([short_month(m["month"]), fmt(m["max_load_mw"]), fmt(m["load_gap_mw"]), fmt(m["reserve_up_gap_mw"]), fmt(m["reserve_emer_gap_mw"]), fmt(m["shortage_gwh"]), severity(gap, m["shortage_gwh"])])
    td = facts.get("typical_day", {})
    table12 = [["小时", "负荷需求（MW）", "新能源出力（MW）", "缺口指标（MW）"]]
    for hour, load, renewable_value, gap in zip(range(24), td.get("load_mw", []), td.get("renewable_mw", []), td.get("gap_mw", [])):
        table12.append([str(hour), fmt(load), fmt(renewable_value), fmt(gap)])
    table12 += [
        ["日内高峰(8-12时)", fmt(td.get("day_peak_load_mw", 0)), fmt(td.get("day_peak_renewable_mw", 0)), fmt(td.get("day_peak_gap_mw", 0))],
        ["晚高峰(18-21时)", fmt(td.get("evening_peak_load_mw", 0)), fmt(td.get("evening_peak_renewable_mw", 0)), fmt(td.get("evening_peak_gap_mw", 0))],
    ]
    table13 = [
        ["问题类型", "具体表现", "影响判断", "关注环节"],
        ["新能源占比较高", f"风光装机占比 {pct(facts['renewable_capacity_share'])}", "提升消纳与备用协同要求", "源荷时序匹配"],
        ["备用能力不足", f"旋备缺口 {fmt(facts['reserve_up_gap_mw'])} MW，停备/紧急备用缺口 {fmt(facts['reserve_emer_gap_mw'])} MW", "可靠性约束主要由备用松弛驱动", "备用配置"],
        ["调节资源支撑不足", f"灵活性资源占比 {pct(facts['flex_capacity_share'])}", "高峰和新能源低出力时段调节压力较大", "灵活性资源"],
        ["经济性受惩罚项影响", f"惩罚性成本约 {fmt(penalty_yuan / 100000000)} 亿元", "备用不足会抬升系统综合成本", "规划经济性"],
    ]
    table14 = [
        ["资源类型", "装机容量（MW）", "调节能力评价", "规划作用", "备注"],
        ["煤电", fmt(cap_value(cap_rows, "煤电")), "中等", "提供可靠容量和一定调峰能力", "受最小出力和启停约束影响"],
        ["气电", fmt(cap_value(cap_rows, "气电")), "较强", "承担快速调峰和备用支撑", "适合覆盖尖峰和备用缺口"],
        ["水电", fmt(cap_value(cap_rows, "三段式水电")), "较强", "提供灵活调节和电量补充", "受来水边界影响"],
        ["储能", fmt(cap_value(cap_rows, "储能")), "强", "平滑净负荷并提供快速备用", "需结合时长和循环约束校核"],
        ["需求响应", "按场景配置", "中等", "削减尖峰负荷和备用压力", "需明确可调用规模和持续时间"],
    ]
    table15 = [
        ["措施", "建议内容", "预期效果", "优先级"],
        ["增加储能配置", "围绕高风险月份校核功率和时长组合，优先覆盖备用缺口", "平抑新能源波动并提供快速备用", "高"],
        ["增加气电/抽蓄", "围绕高峰和停备/紧急备用缺口配置可靠容量", "提升快速调节和事故备用能力", "高"],
        ["优化备用策略", "细化日前、日内备用分配和需求响应触发条件", "降低备用松弛和惩罚成本", "中高"],
        ["扩大需求侧响应", "增加高峰时段可调用负荷资源并明确持续时间", "削峰填谷，缓解晚高峰供需压力", "中高"],
        ["强化跨区互济", "评估外受电通道和邻区支撑能力", "提高极端场景下余缺互济能力", "中"],
        ["开展敏感性校核", "补充极端天气、低新能源出力、检修和负荷高增长情景", "提升规划方案鲁棒性", "中"],
    ]

    paragraphs = {
        "0": f"【{region}】电力系统规划\n中长期电力电量平衡仿真分析报告",
        "5": "图1  系统拓扑图",
        "6": f"本算例为{region}电力规划算例，用于评估{year}水平年电力电量平衡情况。算例呈现出煤电、光伏与风电共同支撑供需平衡的结构特征，新能源装机占比达到{pct(facts['renewable_capacity_share'])}，调节资源与备用约束是影响运行可靠性和经济性的关键因素。",
        "14": f"系统电源结构特点：煤电承担基荷和系统支撑，风光装机合计占比{pct(facts['renewable_capacity_share'])}，新能源已成为重要电量来源；气电、水电、抽蓄和储能构成主要灵活性资源，但从备用松弛结果看，高峰月份仍存在调节能力不足。",
        "16": f"本算例识别到{facts['zone_count']}个分区。当前结果以{region}分区为主，分区装机统计见表4；若后续算例包含多个区域，应重点比较各分区新能源占比、可靠容量和外送/受电关系。",
        "24": f"【关键发现】系统全年负荷缺额为{fmt(facts['energy_shortage_mwh'])} MWh，但存在备用容量缺口：最大旋备缺口约{fmt(facts['reserve_up_gap_mw'])} MW，最大停备缺口约{fmt(facts['reserve_emer_gap_mw'])} MW，风险主要体现为保供备用不足而非电量硬缺口。",
        "39": f"新能源弃电主要受月度风光出力和系统调节能力共同影响。全年风电弃电{fmt(as_float(ren.get('wind_curt')) / 1000)} GWh，光伏弃电{fmt(as_float(ren.get('solar_curt')) / 1000)} GWh；整体弃电规模不大，但备用缺口说明新能源高占比下仍需同步补强灵活性资源。",
        "42": f"【消纳结论】新能源消纳整体{ren_text}，风光合计利用率为{pct(as_float(ren_rate.get('renewable')))}。后续应重点关注高负荷且新能源出力不足的月份，以及高新能源出力但调节资源受限的时段。",
        "53": f"全年最大缺口月份：{facts['max_gap_month'] or '未识别'}，最大缺口功率约{fmt(facts['max_gap_mw'])} MW。典型日图表基于该风险月份附近小时结果提取，用于观察日内负荷、风光出力和缺口的相对关系。",
        "57": "新能源出力与负荷高峰存在时段错配，尤其在晚高峰需要依赖煤电、气电、储能和需求响应共同补足。",
        "58": "系统备用能力不足是本算例的主要约束，旋备和停备松弛成本抬升了综合运行成本。",
        "59": "3. 建议进一步开展储能时长、气电可靠容量和需求响应规模的敏感性校核。",
        "75": f"【结论1】系统{'存在' if facts['max_gap_mw'] else '无'}显著电力缺口：全年最大缺口约{fmt(facts['max_gap_mw'])} MW（{facts['max_gap_month'] or '未识别'}），全年电量缺口{fmt(facts['energy_shortage_mwh'] / 1000)} GWh。",
        "76": f"【结论2】新能源消纳{ren_text}：风光合计利用率{pct(as_float(ren_rate.get('renewable')))}，风光年发电量约{fmt(as_float(ren.get('renewable_acc')) / 1000)} GWh。",
        "77": f"【结论3】系统灵活性{flex_text}：灵活性资源装机占比约{pct(facts['flex_capacity_share'])}，但最大旋备/停备缺口仍分别达到{fmt(facts['reserve_up_gap_mw'])} MW和{fmt(facts['reserve_emer_gap_mw'])} MW。",
        "78": f"【结论4】经济性需优化：惩罚性成本约{fmt(penalty_yuan / 100000000)}亿元，主要与备用松弛相关，应通过可靠容量、储能和需求响应协同降低。",
        "66": f"【煤电】利用小时数：{fmt(cap_value(cap_rows, '煤电', 'util_hour'))} h；承担基荷和系统支撑，是本算例中最主要的可靠容量来源，同时受调峰深度和启停成本约束。",
        "67": f"【水电】利用小时数：{fmt(cap_value(cap_rows, '三段式水电', 'util_hour'))} h；具备较好调节能力，但可用出力受来水、检修和调度边界影响。",
        "68": f"【风电】利用小时数：{fmt(cap_value(cap_rows, '风电', 'util_hour'))} h；年发电量贡献较高，但出力波动增加备用需求。",
        "69": f"【光伏】利用小时数：{fmt(cap_value(cap_rows, '光伏', 'util_hour'))} h；白天出力明显，晚高峰支撑有限，需要储能、气电和需求响应配合。",
        "83": f"建议一：优先校核储能功率与时长组合。当前储能装机{fmt(cap_value(cap_rows, '储能'))} MW，已具备较强日内移峰能力，但{facts['max_gap_month'] or '风险月份'}最大综合缺口约{fmt(facts['max_gap_mw'])} MW，说明仅有功率规模还不够，需要验证连续放电时长、充电窗口、循环约束和备用留存策略。建议围绕4小时、6小时及更长时长方案开展敏感性分析，比较其对晚高峰净负荷、备用缺口和备用惩罚成本的削减效果。",
        "84": f"建议二：补强气电、抽蓄和可调煤电等可靠容量。气电装机{fmt(cap_value(cap_rows, '气电'))} MW、利用小时约{fmt(cap_value(cap_rows, '气电', 'util_hour'))} h，体现其主要价值不在年度发电量，而在高峰调节和备用支撑。对于无电量缺口但有备用松弛的情形，应评估新增气电、抽水蓄能、煤电灵活性改造和机组检修优化的组合方案，重点比较可用容量、启动速度、爬坡能力和单位备用成本。",
        "85": f"建议三：完善需求响应的可调用机制。若算例已配置需求侧响应，仍需结合{facts['max_gap_month'] or '高风险时段'}约{fmt(facts['max_gap_mw'])} MW的综合缺口校核实际可调用能力。建议将可中断负荷、可移峰工业负荷、空调负荷聚合和用户侧储能纳入统一资源池，并明确响应持续时间、提前通知时间、补偿价格和履约考核，使需求侧资源能够稳定计入备用。",
        "86": "建议四：将跨区互济作为备用资源组合的一部分进行校核。浙江作为负荷大省，单省内完全依靠本地资源覆盖极端晚峰备用会推高冗余配置成本。建议结合华东区域电源互补、外受电通道能力和省间交易机制，开展低风低光、高负荷、通道受限等情景下的互济能力评估。跨区互济不能替代本地可靠容量，但可降低孤立规划下的备用惩罚和峰时资源冗余。",
        "87": "其他说明：本报告为基于TEAP计算结果的规划类仿真分析，结论适用于规划水平年的中长期电力电量平衡判断。若用于正式评审，建议补充极端天气、偏枯来水、机组检修、外受电受限、低风低光和新能源预测偏差等情景的鲁棒性校核，并结合工程边界复核资源落地可行性。",
    }
    return {"paragraphs": paragraphs, "tables": {str(i + 1): table for i, table in enumerate([table1, table2, table3, table4, table5, table6, table7, table8, table9, table10, table11, table12, table13, table14, table15])}}


def build_render_content(facts: dict[str, Any], content_json: Path | None, *, allow_content_tables: bool) -> dict[str, Any]:
    content = draft_content(facts)
    if content_json is None:
        return content
    supplied = load_content(content_json)
    content["paragraphs"].update(
        {str(key): str(value) for key, value in (supplied.get("paragraphs") or {}).items() if as_int(key) in AGENT_PARAGRAPH_INDICES}
    )
    if allow_content_tables and supplied.get("tables"):
        validate_content_tables(supplied["tables"], content["tables"])
        content["tables"] = supplied["tables"]
    return content


def validate_content_tables(supplied: dict[str, Any], canonical: dict[str, list[list[Any]]]) -> None:
    problems = []
    for table_no, canonical_rows in canonical.items():
        rows = supplied.get(table_no)
        if rows is None:
            problems.append(f"table {table_no}: missing")
            continue
        if not rows:
            problems.append(f"table {table_no}: empty")
            continue
        if rows[0] != canonical_rows[0]:
            problems.append(f"table {table_no}: header mismatch")
        expected_width = len(canonical_rows[0])
        bad_widths = sorted({len(row) if isinstance(row, list) else -1 for row in rows if not isinstance(row, list) or len(row) != expected_width})
        if bad_widths:
            problems.append(f"table {table_no}: row widths must all be {expected_width}, got {bad_widths}")
    extra = sorted(set(supplied) - set(canonical), key=str)
    if extra:
        problems.append(f"unexpected tables: {', '.join(extra)}")
    if problems:
        raise SystemExit("Invalid content tables; use deterministic tables or fix schema:\n" + "\n".join(problems))


def topology_summary(topology: dict[str, Any]) -> str:
    raw_nodes = topology.get("raw_node_count", len(topology.get("nodes") or []))
    raw_edges = topology.get("raw_edge_count", len(topology.get("edges") or []))
    note = topology.get("filter_note") or topology.get("source") or "-"
    return f"节点 {raw_nodes} 个，线路 {raw_edges} 条；{note}"


def generation_comment(row: dict[str, Any]) -> str:
    capacity = as_float(row.get("capacity_mw"))
    energy = as_float(row.get("energy_mwh"))
    hours = as_float(row.get("util_hour"))
    if capacity <= 0 and energy <= 0:
        return "本算例未配置或贡献较低"
    if row.get("type") in {"风电", "光伏"}:
        return "新能源电量贡献，需关注消纳与备用协同"
    if row.get("type") in {"煤电", "核电"}:
        return "承担可靠容量和基础电量支撑"
    if hours > 3000:
        return "利用水平较高"
    return "主要提供调节或补充支撑"


def cost_comment(item: dict[str, Any]) -> str:
    name = str(item.get("name", ""))
    if "惩罚" in name:
        return "反映约束松弛或可靠性不足成本"
    if "新能源" in name:
        return "与新能源建设运行及消纳约束相关"
    if "储能" in name:
        return "与灵活性资源配置相关"
    return "系统正常运行成本"


def renewable_comment(util_rate: float) -> str:
    if util_rate >= 0.99:
        return "消纳充分"
    if util_rate >= 0.95:
        return "总体较好"
    return "存在消纳压力"


def monthly_renewable_comment(month: dict[str, Any]) -> str:
    return renewable_comment(min(month["wind_util_rate"], month["solar_util_rate"]))


def risk_type(facts: dict[str, Any]) -> str:
    if facts["energy_shortage_mwh"] > 0:
        return "电量不足风险"
    if facts["reserve_up_gap_mw"] or facts["reserve_emer_gap_mw"]:
        return "备用可靠性风险"
    if facts["max_gap_mw"]:
        return "容量缺口风险"
    return "总体平衡"


def render_docx(template: Path, output: Path, content: dict[str, Any], figures: dict[str, Path]) -> None:
    with zipfile.ZipFile(template, "r") as zin:
        document_root = ET.fromstring(zin.read("word/document.xml"))
        rels_root = ET.fromstring(zin.read("word/_rels/document.xml.rels"))
        content_types_root = ET.fromstring(zin.read("[Content_Types].xml"))
        files = {item.filename: zin.read(item.filename) for item in zin.infolist()}
        infos = {item.filename: item for item in zin.infolist()}

    body = document_root.find("w:body", NS)
    if body is None:
        raise SystemExit("Template document.xml has no body")
    body_items = list(body)
    tables = [item for item in body_items if item.tag == f"{{{W_NS}}}tbl"]

    for index_text, text in (content.get("paragraphs") or {}).items():
        idx = int(index_text)
        if 0 <= idx < len(body_items) and body_items[idx].tag == f"{{{W_NS}}}p":
            set_paragraph_text(body_items[idx], str(text))
            if idx in ANALYSIS_PARAGRAPH_INDICES:
                apply_first_line_indent(body_items[idx])
    if len(body_items) > 5 and body_items[5].tag == f"{{{W_NS}}}p":
        set_paragraph_text(body_items[5], "图1  系统拓扑图")

    for table_no_text, rows in (content.get("tables") or {}).items():
        table_no = int(table_no_text)
        if 1 <= table_no <= len(tables):
            replace_table(tables[table_no - 1], rows)

    next_rid = next_relationship_id(rels_root)
    image_insertions: dict[int, tuple[str, Path, int, int]] = {}
    for figure_no, image_path in figures.items():
        caption_index = figure_caption_index(int(figure_no))
        if caption_index is None:
            continue
        rid = f"rId{next_rid}"
        next_rid += 1
        add_image_relationship(rels_root, rid, f"media/{image_path.name}")
        width_px, height_px = image_display_size(image_path, target_width_px=620, max_height_px=420)
        image_insertions[caption_index] = (rid, image_path, width_px, height_px)

    for caption_index in sorted(image_insertions, reverse=True):
        rid, image_path, width_px, height_px = image_insertions[caption_index]
        image_p = build_image_paragraph(rid, image_path.name, width_px, height_px)
        body.insert(caption_index + 1, image_p)

    normalize_body_paragraph_indents(body)
    ensure_png_content_type(content_types_root)
    files["word/document.xml"] = ensure_document_namespaces(
        ET.tostring(document_root, encoding="utf-8", xml_declaration=True)
    )
    files["word/_rels/document.xml.rels"] = ET.tostring(rels_root, encoding="utf-8", xml_declaration=True)
    files["[Content_Types].xml"] = ET.tostring(content_types_root, encoding="utf-8", xml_declaration=True)
    for image_path in figures.values():
        files[f"word/media/{image_path.name}"] = image_path.read_bytes()

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zout:
        for filename, data in files.items():
            info = infos.get(filename, zipfile.ZipInfo(filename))
            zout.writestr(info, data)


def set_paragraph_text(paragraph: ET.Element, text: str) -> None:
    text = sanitize_report_text(text)
    texts = paragraph.findall(".//w:t", NS)
    if not texts:
        run = ET.SubElement(paragraph, f"{{{W_NS}}}r")
        t = ET.SubElement(run, f"{{{W_NS}}}t")
        texts = [t]
    lines = str(text).split("\n")
    first_t = texts[0]
    first_t.text = lines[0]
    first_t.set(f"{{{XML_NS}}}space", "preserve")
    for extra in texts[1:]:
        extra.text = ""
    parent_run = find_parent(paragraph, first_t)
    if parent_run is not None:
        for line in lines[1:]:
            ET.SubElement(parent_run, f"{{{W_NS}}}br")
            t = ET.SubElement(parent_run, f"{{{W_NS}}}t")
            t.text = line
            t.set(f"{{{XML_NS}}}space", "preserve")


def sanitize_report_text(text: str) -> str:
    cleaned: list[str] = []
    for char in str(text):
        code_point = ord(char)
        if any(start <= code_point <= end for start, end in EMOJI_RANGES):
            continue
        cleaned.append(char)
    return "".join(cleaned)


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def apply_first_line_indent(paragraph: ET.Element) -> None:
    p_pr = paragraph.find("w:pPr", NS)
    if p_pr is None:
        p_pr = ET.Element(f"{{{W_NS}}}pPr")
        paragraph.insert(0, p_pr)
    ind = p_pr.find("w:ind", NS)
    if ind is None:
        ind = ET.Element(f"{{{W_NS}}}ind")
        insert_paragraph_property(p_pr, ind, after_tags={f"{{{W_NS}}}spacing"})
    ind.set(f"{{{W_NS}}}firstLineChars", "200")
    ind.attrib.pop(f"{{{W_NS}}}firstLine", None)
    ind.attrib.pop(f"{{{W_NS}}}hanging", None)
    ind.attrib.pop(f"{{{W_NS}}}hangingChars", None)


def insert_paragraph_property(p_pr: ET.Element, child: ET.Element, *, after_tags: set[str]) -> None:
    insert_at = 0
    for idx, item in enumerate(list(p_pr)):
        if item.tag in after_tags:
            insert_at = idx + 1
    p_pr.insert(insert_at, child)


def normalize_body_paragraph_indents(body: ET.Element) -> None:
    in_section_six = False
    for paragraph in body.findall("w:p", NS):
        if paragraph.find(".//w:drawing", NS) is not None:
            continue
        text = "".join(t.text or "" for t in paragraph.findall(".//w:t", NS)).strip()
        if text.startswith("六、结论与建议"):
            in_section_six = True
            normalize_section_six_indent(paragraph, text)
            continue
        if in_section_six:
            normalize_section_six_indent(paragraph, text)
            continue
        if should_indent_body_text(text):
            apply_first_line_indent(paragraph)


def remove_first_line_indent(paragraph: ET.Element) -> None:
    ind = paragraph.find("w:pPr/w:ind", NS)
    if ind is None:
        return
    for attr in ["firstLineChars", "firstLine", "hanging", "hangingChars"]:
        ind.attrib.pop(f"{{{W_NS}}}{attr}", None)


def normalize_section_six_indent(paragraph: ET.Element, text: str) -> None:
    remove_before_text_indent(paragraph)
    if should_indent_body_text(text):
        apply_first_line_indent(paragraph)
    else:
        remove_first_line_indent(paragraph)


def remove_before_text_indent(paragraph: ET.Element) -> None:
    ind = paragraph.find("w:pPr/w:ind", NS)
    if ind is None:
        return
    for attr in ["left", "leftChars"]:
        ind.attrib.pop(f"{{{W_NS}}}{attr}", None)


def should_indent_body_text(text: str) -> bool:
    if not text or len(text) < 45:
        return False
    if text.startswith(("图", "表")):
        return False
    if re.fullmatch(r"[一二三四五六七八九十]+、.*", text):
        return False
    if re.fullmatch(r"\d+(\.\d+)*\s+.*", text):
        return False
    if "仿真分析报告" in text and len(text) < 80:
        return False
    return True


def find_parent(root: ET.Element, target: ET.Element) -> ET.Element | None:
    for item in root.iter():
        for child in list(item):
            if child is target:
                return item
    return None


def replace_table(table: ET.Element, rows: list[list[Any]]) -> None:
    existing = table.findall("w:tr", NS)
    if not existing:
        return
    header_template = deepcopy(existing[0])
    data_template = deepcopy(existing[1] if len(existing) > 1 else existing[0])
    for row in existing:
        table.remove(row)
    for idx, values in enumerate(rows):
        row = deepcopy(header_template if idx == 0 else data_template)
        cells = row.findall("w:tc", NS)
        while len(cells) < len(values):
            row.append(deepcopy(cells[-1]))
            cells = row.findall("w:tc", NS)
        for cell, value in zip(cells, values):
            set_cell_text(cell, str(value))
        for cell in cells[len(values):]:
            set_cell_text(cell, "")
        table.append(row)
    apply_three_line_table(table)


def set_cell_text(cell: ET.Element, text: str) -> None:
    paragraphs = cell.findall("w:p", NS)
    if not paragraphs:
        paragraphs = [ET.SubElement(cell, f"{{{W_NS}}}p")]
    set_paragraph_text(paragraphs[0], text)
    for p in paragraphs[1:]:
        set_paragraph_text(p, "")


def apply_three_line_table(table: ET.Element) -> None:
    rows = table.findall("w:tr", NS)
    if not rows:
        return
    for row_index, row in enumerate(rows):
        is_header = row_index == 0
        is_last = row_index == len(rows) - 1
        for cell in row.findall("w:tc", NS):
            tc_pr = cell.find("w:tcPr", NS)
            if tc_pr is None:
                tc_pr = ET.Element(f"{{{W_NS}}}tcPr")
                cell.insert(0, tc_pr)
            existing = tc_pr.find("w:tcBorders", NS)
            if existing is not None:
                tc_pr.remove(existing)
            borders = ET.Element(f"{{{W_NS}}}tcBorders")
            borders.append(border("top", "single" if is_header else "nil", size=12 if is_header else 0))
            borders.append(border("left", "nil"))
            borders.append(border("bottom", "single" if (is_header or is_last) else "nil", size=12 if is_last else 6))
            borders.append(border("right", "nil"))
            insert_after_tc_width(tc_pr, borders)


def border(name: str, value: str, *, size: int = 0) -> ET.Element:
    item = ET.Element(f"{{{W_NS}}}{name}")
    item.set(f"{{{W_NS}}}val", value)
    if value != "nil":
        item.set(f"{{{W_NS}}}color", "000000")
        item.set(f"{{{W_NS}}}sz", str(size or 6))
        item.set(f"{{{W_NS}}}space", "0")
    return item


def insert_after_tc_width(tc_pr: ET.Element, child: ET.Element) -> None:
    insert_at = 0
    for idx, item in enumerate(list(tc_pr)):
        if item.tag in {f"{{{W_NS}}}tcW", f"{{{W_NS}}}gridSpan", f"{{{W_NS}}}vMerge"}:
            insert_at = idx + 1
    tc_pr.insert(insert_at, child)


def next_relationship_id(rels_root: ET.Element) -> int:
    max_id = 0
    for rel in rels_root.findall("rel:Relationship", NS):
        rid = rel.get("Id", "")
        match = re.fullmatch(r"rId(\d+)", rid)
        if match:
            max_id = max(max_id, int(match.group(1)))
    return max_id + 1


def add_image_relationship(rels_root: ET.Element, rid: str, target: str) -> None:
    rel = ET.SubElement(rels_root, f"{{{REL_NS}}}Relationship")
    rel.set("Id", rid)
    rel.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image")
    rel.set("Target", target)


def ensure_png_content_type(root: ET.Element) -> None:
    for default in root.findall("ct:Default", NS):
        if default.get("Extension") == "png":
            return
    item = ET.Element(f"{{{CT_NS}}}Default")
    item.set("Extension", "png")
    item.set("ContentType", "image/png")
    root.insert(0, item)


def build_image_paragraph(rid: str, name: str, width_px: int, height_px: int) -> ET.Element:
    cx = width_px * EMU_PER_PX
    cy = height_px * EMU_PER_PX
    xml = f"""
    <w:p xmlns:w="{W_NS}" xmlns:r="{R_NS}" xmlns:wp="{WP_NS}" xmlns:a="{A_NS}" xmlns:pic="{PIC_NS}">
      <w:pPr><w:jc w:val="center"/></w:pPr>
      <w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">
        <wp:extent cx="{cx}" cy="{cy}"/>
        <wp:docPr id="{abs(hash(name)) % 100000}" name="{name}"/>
        <wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>
        <a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
          <pic:pic>
            <pic:nvPicPr><pic:cNvPr id="0" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>
            <pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
            <pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>
          </pic:pic>
        </a:graphicData></a:graphic>
      </wp:inline></w:drawing></w:r>
    </w:p>
    """
    return ET.fromstring(xml)


def image_display_size(path: Path, *, target_width_px: int, max_height_px: int) -> tuple[int, int]:
    with Image.open(path) as image:
        width, height = image.size
    if width <= 0 or height <= 0:
        return target_width_px, int(target_width_px * 0.62)
    display_width = target_width_px
    display_height = int(target_width_px * height / width)
    if display_height > max_height_px:
        display_height = max_height_px
        display_width = int(max_height_px * width / height)
    return display_width, display_height


def ensure_document_namespaces(xml_bytes: bytes) -> bytes:
    text = xml_bytes.decode("utf-8")
    insert = []
    for prefix, uri in [("w15", W15_NS), ("wp14", WP14_NS)]:
        if f"xmlns:{prefix}=" not in text:
            insert.append(f' xmlns:{prefix}="{uri}"')
    if insert:
        text = text.replace("<w:document ", "<w:document " + "".join(insert) + " ", 1)
    return text.encode("utf-8")


def figure_caption_index(figure_no: int) -> int | None:
    return {1: 5, 2: 10, 3: 21, 4: 26, 5: 30, 6: 35, 7: 45, 8: 52, 9: 62}.get(figure_no)


def build_figures(facts: dict[str, Any], figure_dir: Path) -> dict[str, Path]:
    figure_dir.mkdir(parents=True, exist_ok=True)
    seaborn_figures = build_figures_with_seaborn(facts, figure_dir)
    if seaborn_figures is not None:
        return seaborn_figures
    raise SystemExit("Template report figure generation requires seaborn and matplotlib. Install them with `uv add seaborn matplotlib`.")


def build_figures_with_seaborn(facts: dict[str, Any], figure_dir: Path) -> dict[str, Path] | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        import seaborn as sns
        from matplotlib import font_manager
    except Exception:
        return None

    ensure_report_font_runtime(font_manager=font_manager)
    cjk_path = resolve_matplotlib_cjk_font(figure_dir, font_manager=font_manager)
    if not cjk_path:
        raise SystemExit(
            "Template report figure generation requires a CJK font. "
            f"Install {' '.join(REPORT_FONT_APT_PACKAGES)} on Debian/Ubuntu, "
            f"or set {REPORT_CHART_CJK_FONT_PATH_ENV}/{REPORT_FONT_DIR_ENV}."
        )
    latin_path = resolve_matplotlib_latin_font(font_manager=font_manager)
    cjk = font_manager.FontProperties(fname=cjk_path) if cjk_path else None
    latin = font_manager.FontProperties(fname=latin_path) if latin_path else None

    if cjk_path:
        font_manager.fontManager.addfont(cjk_path)
    if latin_path:
        font_manager.fontManager.addfont(latin_path)
    (figure_dir / "font_selection.json").write_text(
        json.dumps(
            {
                "cjk_path": cjk_path,
                "cjk_name": cjk.get_name() if cjk else None,
                "latin_path": latin_path,
                "latin_name": latin.get_name() if latin else None,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    sns.set_theme(style="whitegrid", context="notebook", palette="deep")
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [
                cjk.get_name() if cjk else "Noto Sans CJK SC",
                "Noto Sans CJK SC",
                "SimHei",
                "Microsoft YaHei",
                "DejaVu Sans",
            ],
            "font.serif": [
                latin.get_name() if latin else "Liberation Serif",
                "Times New Roman",
                "Liberation Serif",
                "DejaVu Serif",
            ],
            "axes.unicode_minus": False,
            "figure.dpi": 160,
            "savefig.dpi": 180,
        }
    )

    def new_fig(_title: str, *, polar: bool = False):
        fig = plt.figure(figsize=(8.8, 4.4), constrained_layout=True)
        ax = fig.add_subplot(111, polar=polar)
        return fig, ax

    def apply_tick_fonts(ax):
        for label in ax.get_xticklabels() + ax.get_yticklabels():
            text = label.get_text()
            label.set_fontproperties(cjk if any(is_cjk_char(char) for char in text) else latin)
            label.set_fontsize(9)

    def save_fig(fig, path: Path) -> Path:
        # Keep the intended landscape canvas ratio. Tight bounding boxes can crop
        # circular charts into near-square images, which then look compressed in Word.
        fig.savefig(path, facecolor="white", pad_inches=0.08)
        plt.close(fig)
        return path

    def month_labels() -> list[str]:
        return [short_month(item["month"]) for item in facts["monthly"]]

    figures: dict[str, Path] = {}

    # Figure 1: topology from actual bus/line data.
    fig, ax = new_fig("系统拓扑图")
    ax.set_axis_off()
    draw_matplotlib_topology(ax, facts, cjk)
    figures["1"] = save_fig(fig, figure_dir / "template_a_fig1_topology.png")

    # Figure 2: capacity donut.
    rows = [row for row in facts["capacity_rows"] if row["capacity_mw"] > 0]
    fig, ax = new_fig("系统装机结构")
    colors = sns.color_palette("Set2", n_colors=len(rows))
    wedges, _texts = ax.pie(
        [row["capacity_mw"] for row in rows],
        startangle=90,
        colors=colors,
        wedgeprops={"width": 0.42, "edgecolor": "white"},
    )
    legend_labels = [f"{row['type']} {pct(row['share'])}" for row in rows]
    ax.legend(wedges, legend_labels, loc="center left", bbox_to_anchor=(0.92, 0.5), prop=cjk, frameon=False)
    ax.set_aspect("equal")
    figures["2"] = save_fig(fig, figure_dir / "template_a_fig2_capacity.png")

    # Figure 3: load and renewable trend.
    fig, ax = new_fig("全年负荷与新能源发电月度趋势")
    x = np.arange(len(facts["monthly"]))
    ax.plot(x, [m["load_energy_gwh"] for m in facts["monthly"]], marker="o", lw=2.2, label="负荷电量")
    ax.plot(x, [m["wind_gwh"] + m["solar_gwh"] for m in facts["monthly"]], marker="s", lw=2.2, label="风光发电")
    ax.set_xticks(x, month_labels(), fontproperties=cjk)
    ax.set_ylabel("GWh", fontproperties=latin)
    ax.legend(prop=cjk, frameon=False)
    apply_tick_fonts(ax)
    figures["3"] = save_fig(fig, figure_dir / "template_a_fig3_load_renewable.png")

    # Figure 4: monthly stacked generation.
    fig, ax = new_fig("全年各月发电量分布")
    bottom = np.zeros(len(facts["monthly"]))
    stack_items = [
        ("煤电", [m["coal_gwh"] for m in facts["monthly"]]),
        ("气电", [m["gas_gwh"] for m in facts["monthly"]]),
        ("水电", [m["hydro_gwh"] for m in facts["monthly"]]),
        ("风电", [m["wind_gwh"] for m in facts["monthly"]]),
        ("光伏", [m["solar_gwh"] for m in facts["monthly"]]),
    ]
    for name, values in stack_items:
        ax.bar(x, values, bottom=bottom, label=name)
        bottom += np.array(values)
    ax.set_xticks(x, month_labels(), fontproperties=cjk)
    ax.set_ylabel("GWh", fontproperties=latin)
    ax.legend(prop=cjk, frameon=False, ncols=5, loc="upper center", bbox_to_anchor=(0.5, -0.08))
    apply_tick_fonts(ax)
    figures["4"] = save_fig(fig, figure_dir / "template_a_fig4_monthly_generation.png")

    # Figure 5: cost composition.
    fig, ax = new_fig("系统成本构成")
    cost_labels = [item["name"] for item in facts["cost_items"]]
    cost_values = [item["value_yuan"] / 100000000 for item in facts["cost_items"]]
    sns.barplot(x=cost_values, y=cost_labels, ax=ax, orient="h", palette="Blues_r", hue=cost_labels, legend=False)
    ax.set_xlabel("亿元", fontproperties=cjk)
    ax.set_ylabel("")
    for label in ax.get_yticklabels():
        label.set_fontproperties(cjk)
    apply_tick_fonts(ax)
    figures["5"] = save_fig(fig, figure_dir / "template_a_fig5_costs.png")

    # Figure 6: renewable accommodation.
    fig, ax = new_fig("新能源消纳情况")
    ren = facts["renewable"]
    labels = ["风电发电", "风电弃电", "光伏发电", "光伏弃电"]
    values = [
        as_float(ren.get("wind_acc")) / 1000,
        as_float(ren.get("wind_curt")) / 1000,
        as_float(ren.get("solar_acc")) / 1000,
        as_float(ren.get("solar_curt")) / 1000,
    ]
    sns.barplot(x=labels, y=values, ax=ax, palette=["#4C78A8", "#F58518", "#54A24B", "#E45756"], hue=labels, legend=False)
    ax.set_ylabel("GWh", fontproperties=latin)
    ax.set_xticks(range(len(labels)), labels, fontproperties=cjk)
    apply_tick_fonts(ax)
    figures["6"] = save_fig(fig, figure_dir / "template_a_fig6_renewable.png")

    # Figure 7: shortage distribution.
    fig, ax = new_fig("月度缺口分布")
    for name, values, color in [
        ("负荷缺口", [m["load_gap_mw"] for m in facts["monthly"]], "#DC2626"),
        ("旋备缺口", [m["reserve_up_gap_mw"] for m in facts["monthly"]], "#F97316"),
        ("停备缺口", [m["reserve_emer_gap_mw"] for m in facts["monthly"]], "#7C3AED"),
    ]:
        ax.plot(x, values, marker="o", lw=2.2, color=color, label=name)
    ax.set_xticks(x, month_labels(), fontproperties=cjk)
    ax.set_ylabel("MW", fontproperties=latin)
    ax.legend(prop=cjk, frameon=False)
    apply_tick_fonts(ax)
    figures["7"] = save_fig(fig, figure_dir / "template_a_fig7_shortage.png")

    # Figure 8: typical day.
    fig, ax = new_fig("典型日负荷、新能源与缺口")
    td = facts.get("typical_day") or {}
    hours = np.arange(24)
    for name, values, color in [
        ("负荷", td.get("load_mw", [0] * 24), "#2563EB"),
        ("风光", td.get("renewable_mw", [0] * 24), "#16A34A"),
        ("缺口", td.get("gap_mw", [0] * 24), "#DC2626"),
    ]:
        ax.plot(hours, values, lw=2.0, label=name, color=color)
    ax.set_xticks(range(0, 24, 3))
    ax.set_xlabel("小时", fontproperties=cjk)
    ax.set_ylabel("MW", fontproperties=latin)
    ax.legend(prop=cjk, frameon=False)
    apply_tick_fonts(ax)
    figures["8"] = save_fig(fig, figure_dir / "template_a_fig8_typical_day.png")

    # Figure 9: actual radar chart.
    radar_labels = ["新能源占比", "灵活性", "备用充裕", "消纳水平", "经济性"]
    radar_values = [
        facts["renewable_capacity_share"],
        facts["flex_capacity_share"],
        max(0.05, 1 - safe_div(max(facts["reserve_up_gap_mw"], facts["reserve_emer_gap_mw"]), max(facts["max_load_mw"], 1))),
        as_float(facts["renewable_rate"].get("renewable")),
        max(0.05, 1 - safe_div(sum(item["value_yuan"] for item in facts["cost_items"] if "惩罚" in item["name"]), max(facts["cost_total_yuan"], 1))),
    ]
    angles = np.linspace(0, 2 * np.pi, len(radar_labels), endpoint=False).tolist()
    values = radar_values + radar_values[:1]
    angles = angles + angles[:1]
    fig, ax = new_fig("电源结构问题雷达图", polar=True)
    ax.plot(angles, values, color="#2563EB", linewidth=2.4)
    ax.fill(angles, values, color="#60A5FA", alpha=0.30)
    ax.set_ylim(0, 1)
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["25%", "50%", "75%", "100%"], fontproperties=latin, fontsize=9)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(radar_labels, fontproperties=cjk, fontsize=11)
    ax.grid(True, color="#CBD5E1", linewidth=1)
    figures["9"] = save_fig(fig, figure_dir / "template_a_fig9_structure.png")

    return figures


def first_existing_path(paths: list[str]) -> str | None:
    for path in paths:
        if Path(path).exists():
            return path
    return None


def resolve_matplotlib_cjk_font(figure_dir: Path, *, font_manager: Any) -> str | None:
    for path_text in cjk_font_candidates(font_manager):
        path = Path(path_text)
        if not path.exists():
            continue
        if path.suffix.lower() == ".ttc":
            extracted = extract_ttc_face(
                path,
                figure_dir / "_fonts",
                preferred_families=[
                    "Noto Sans CJK SC",
                    "Source Han Sans SC",
                    "Noto Serif CJK SC",
                    "Source Han Serif SC",
                ],
            )
            if extracted:
                return str(extracted)
        return str(path)
    return None


def resolve_matplotlib_latin_font(*, font_manager: Any) -> str | None:
    return first_existing_path(latin_font_candidates(font_manager))


def extract_ttc_face(ttc_path: Path, output_dir: Path, *, preferred_families: list[str]) -> Path | None:
    try:
        from fontTools.ttLib import TTCollection
    except Exception:
        return None
    try:
        collection = TTCollection(str(ttc_path))
    except Exception:
        return None
    for font in collection.fonts:
        family_names = font_family_names(font)
        matched = next((name for name in preferred_families if name in family_names), "")
        if not matched:
            continue
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"{matched.replace(' ', '')}-Regular.otf"
        if not target.exists():
            font.save(str(target))
        return target
    return None


def font_family_names_for_path(path: Path) -> set[str]:
    try:
        if path.suffix.lower() == ".ttc":
            from fontTools.ttLib import TTCollection

            names: set[str] = set()
            for font in TTCollection(str(path)).fonts:
                names.update(font_family_names(font))
            return names

        from fontTools.ttLib import TTFont

        with TTFont(str(path), lazy=True) as font:
            return font_family_names(font)
    except Exception:
        return set()


def font_family_names(font: Any) -> set[str]:
    names: set[str] = set()
    try:
        for item in font["name"].names:
            if item.nameID in {1, 4}:
                names.add(item.toUnicode())
    except Exception:
        return names
    return names


def draw_matplotlib_topology(ax: Any, facts: dict[str, Any], cjk: Any) -> None:
    topology = facts.get("topology") or {}
    nodes = topology.get("nodes") or []
    edges = topology.get("edges") or []
    if not nodes:
        ax.text(0.5, 0.5, "未抽取到节点数据", ha="center", va="center", fontproperties=cjk, fontsize=13, transform=ax.transAxes)
        return

    positions = topology_positions(nodes)
    for edge in edges:
        p1 = positions.get(edge.get("from_bus"))
        p2 = positions.get(edge.get("to_bus"))
        if not p1 or not p2:
            continue
        color = "#2563EB" if edge.get("kind") == "DC" else "#64748B"
        ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color=color, linewidth=1.6, alpha=0.75, zorder=1)

    voltages = [as_float(node.get("vn_kv")) for node in nodes]
    max_v = max(voltages or [1]) or 1
    for node in nodes:
        x, y = positions[node["index"]]
        voltage = as_float(node.get("vn_kv"))
        size = 70 + 130 * safe_div(voltage, max_v)
        color = "#DC2626" if voltage >= 500 else "#2563EB" if voltage >= 220 else "#16A34A"
        ax.scatter([x], [y], s=size, color=color, edgecolor="white", linewidth=1.2, zorder=3)
        if len(nodes) <= 35 or voltage >= 500:
            ax.text(x, y + 0.045, str(node.get("name") or node.get("index")), ha="center", va="bottom", fontproperties=cjk, fontsize=9)

    if len(nodes) == 1:
        node = nodes[0]
        ax.text(
            0.5,
            0.20,
            f"单节点拓扑：{node.get('name')}，电压等级 {fmt(as_float(node.get('vn_kv')))} kV；本结果未包含 AC/DC 线路记录。",
            ha="center",
            fontproperties=cjk,
            fontsize=11,
            transform=ax.transAxes,
        )
    note = topology.get("filter_note", "")
    ax.text(
        0.5,
        0.05,
        f"节点 {topology.get('raw_node_count', len(nodes))} 个，线路 {topology.get('raw_edge_count', len(edges))} 条；{note}",
        ha="center",
        fontproperties=cjk,
        fontsize=10,
        color="#475569",
        transform=ax.transAxes,
    )
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)


def topology_positions(nodes: list[dict[str, Any]]) -> dict[int, tuple[float, float]]:
    geo_nodes = [
        node
        for node in nodes
        if math.isfinite(as_float(node.get("lon"), default=float("nan"))) and math.isfinite(as_float(node.get("lat"), default=float("nan")))
    ]
    if len(geo_nodes) >= max(2, len(nodes) // 2):
        lons = [as_float(node.get("lon")) for node in geo_nodes]
        lats = [as_float(node.get("lat")) for node in geo_nodes]
        lon_min, lon_max = min(lons), max(lons)
        lat_min, lat_max = min(lats), max(lats)
        positions = {}
        for node in nodes:
            lon = as_float(node.get("lon"), default=float("nan"))
            lat = as_float(node.get("lat"), default=float("nan"))
            if math.isfinite(lon) and math.isfinite(lat):
                x = 0.10 + 0.80 * safe_div(lon - lon_min, lon_max - lon_min)
                y = 0.12 + 0.76 * safe_div(lat - lat_min, lat_max - lat_min)
            else:
                x, y = circular_position(len(positions), len(nodes))
            positions[node["index"]] = (x, y)
        return positions
    return {node["index"]: circular_position(i, len(nodes)) for i, node in enumerate(nodes)}


def circular_position(i: int, total: int) -> tuple[float, float]:
    if total <= 1:
        return (0.5, 0.55)
    angle = 2 * math.pi * i / total - math.pi / 2
    return (0.5 + 0.34 * math.cos(angle), 0.52 + 0.34 * math.sin(angle))


def configured_font_paths(env_name: str) -> list[str]:
    raw = normalize_text(os.environ.get(env_name))
    if not raw:
        return []
    return [item.strip() for item in raw.split(os.pathsep) if item.strip()]


def configured_font_dirs() -> list[Path]:
    raw = normalize_text(os.environ.get(REPORT_FONT_DIR_ENV))
    configured = [Path(item).expanduser() for item in raw.split(os.pathsep) if item.strip()] if raw else []
    home = Path.home()
    defaults = [
        home / ".clawui_release" / "report-fonts",
        home / ".clawui" / "report-fonts",
        home / ".local" / "share" / "fonts",
        home / ".fonts",
        Path("/root/.clawui_release/report-fonts"),
        Path("/usr/local/share/fonts"),
        Path("/usr/share/fonts"),
    ]
    return dedupe_path_objects(configured + defaults)


def configured_font_dir_files(filename_keywords: list[str]) -> list[str]:
    found: list[str] = []
    normalized_keywords = [item.lower().replace("-", "").replace("_", "") for item in filename_keywords]
    for font_dir in configured_font_dirs():
        try:
            if not font_dir.exists():
                continue
            candidates = [
                path
                for path in font_dir.rglob("*")
                if path.is_file() and path.suffix.lower() in {".ttf", ".ttc", ".otf"}
            ]
        except OSError:
            continue
        for path in candidates:
            normalized_name = path.name.lower().replace("-", "").replace("_", "")
            if any(keyword in normalized_name for keyword in normalized_keywords):
                found.append(str(path))
    return found


def cjk_font_candidates(font_manager: Any) -> list[str]:
    return dedupe_paths(
        configured_font_paths(REPORT_CHART_CJK_FONT_PATH_ENV)
        + configured_font_dir_files(["notosanscjk", "sourcehan", "simhei", "simsun", "fangsong", "wqy", "wenquanyi"])
        + hei_font_candidates()
        + discover_system_font_paths(
            font_manager,
            preferred_families=[
                "Noto Sans CJK SC",
                "Source Han Sans SC",
                "Noto Serif CJK SC",
                "Source Han Serif SC",
                "SimHei",
                "Microsoft YaHei",
                "WenQuanYi Micro Hei",
            ],
            filename_keywords=["notosanscjk", "sourcehan", "simhei", "wqy", "wenquanyi"],
        )
    )


def latin_font_candidates(font_manager: Any) -> list[str]:
    return dedupe_paths(
        configured_font_paths(REPORT_CHART_LATIN_FONT_PATH_ENV)
        + configured_font_dir_files(["times", "liberationserif", "dejavuserif"])
        + preferred_latin_font_paths()
        + discover_system_font_paths(
            font_manager,
            preferred_families=[
                "Times New Roman",
                "Liberation Serif",
                "DejaVu Serif",
            ],
            filename_keywords=["times", "liberationserif", "dejavuserif"],
        )
    )


def hei_font_candidates() -> list[str]:
    return [
        "/usr/share/fonts/truetype/windows/simhei.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/SimHei.ttf",
        "/usr/share/fonts/truetype/microsoft/SimHei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/share/fonts/opentype/source-han-sans/SourceHanSansSC-Regular.otf",
        "/usr/share/fonts/opentype/source-han-serif/SourceHanSerifSC-Regular.otf",
    ]


def preferred_latin_font_paths() -> list[str]:
    # Prefer Times New Roman; Liberation Serif is metric-compatible enough for server rendering.
    return [
        "/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/times.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    ]


def ensure_report_font_runtime(*, font_manager: Any) -> None:
    if first_existing_path(cjk_font_candidates(font_manager)) and first_existing_path(latin_font_candidates(font_manager)):
        return
    if not should_auto_install_report_fonts():
        return
    install_system_report_fonts()
    refresh_matplotlib_font_cache(font_manager)


def should_auto_install_report_fonts() -> bool:
    raw = normalize_text(os.environ.get(REPORT_FONT_AUTO_INSTALL_ENV, "1")).lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0 and bool(shutil.which("apt-get"))


def install_system_report_fonts() -> None:
    env = os.environ.copy()
    env.setdefault("DEBIAN_FRONTEND", "noninteractive")
    try:
        subprocess.run(["apt-get", "update", "-qq"], check=True, env=env, stdout=subprocess.DEVNULL)
        subprocess.run(["apt-get", "install", "-y", "-qq", *REPORT_FONT_APT_PACKAGES], check=True, env=env)
    except Exception as exc:
        print(
            "Warning: failed to auto-install report fonts. "
            f"Please install {' '.join(REPORT_FONT_APT_PACKAGES)} or set {REPORT_CHART_CJK_FONT_PATH_ENV}. "
            f"Detail: {exc}",
            file=sys.stderr,
        )
        return
    if shutil.which("fc-cache"):
        subprocess.run(["fc-cache", "-f"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def refresh_matplotlib_font_cache(font_manager: Any) -> None:
    try:
        font_manager._load_fontmanager(try_read_cache=False)
    except Exception:
        pass


def discover_system_font_paths(
    font_manager: Any,
    *,
    preferred_families: list[str],
    filename_keywords: list[str],
) -> list[str]:
    found: list[str] = []
    preferred = set(preferred_families)
    keywords = [item.lower() for item in filename_keywords]
    for path_text in font_manager.findSystemFonts():
        path = Path(path_text)
        lower_name = path.name.lower().replace("-", "").replace("_", "")
        if any(keyword in lower_name for keyword in keywords):
            found.append(str(path))
            continue
        if font_family_names_for_path(path) & preferred:
            found.append(str(path))
    return found


def dedupe_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for path in paths:
        normalized = str(Path(path).expanduser())
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def dedupe_path_objects(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    deduped: list[Path] = []
    for path in paths:
        normalized = str(path.expanduser())
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(path.expanduser())
    return deduped


def is_cjk_char(char: str) -> bool:
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x3000 <= code <= 0x303F
        or 0xFF00 <= code <= 0xFFEF
    )


def build_typical_day(hourly: list[dict[str, Any]]) -> dict[str, Any]:
    load, renewable, gap = [], [], []
    for item in hourly[:24]:
        rows = item.get("data", [])
        load_value = row_number(rows, "1.本地负荷", "全系统") * 10
        wind = row_number(rows, "1.风电", "全系统") * 10
        solar = row_number(rows, "2.光伏", "全系统") * 10
        g = row_number(rows, "十四、总电力缺口", "全系统") * 10
        load.append(load_value)
        renewable.append(wind + solar)
        gap.append(g)
    while len(load) < 24:
        load.append(0.0)
        renewable.append(0.0)
        gap.append(0.0)
    day_slice = slice(8, 13)
    evening_slice = slice(18, 22)
    return {
        "load_mw": load,
        "renewable_mw": renewable,
        "gap_mw": gap,
        "day_peak_load_mw": max(load[day_slice] or [0]),
        "day_peak_renewable_mw": max(renewable[day_slice] or [0]),
        "day_peak_gap_mw": max(gap[day_slice] or [0]),
        "evening_peak_load_mw": max(load[evening_slice] or [0]),
        "evening_peak_renewable_mw": max(renewable[evening_slice] or [0]),
        "evening_peak_gap_mw": max(gap[evening_slice] or [0]),
    }


def typical_hour_index(power_rows: list[dict[str, Any]]) -> int:
    month, _gap = max_month_pair(power_rows, "十四、总电力缺口")
    if not month:
        month = max_month_pair(power_rows, "2.旋备缺口")[0] or "2030年1月"
    date_row = find_row(power_rows, "日期")
    hour_row = find_row(power_rows, "盈亏控制时段")
    date_text = str(date_row.get(month, "1月1日"))
    hour_text = str(hour_row.get(month, "0时"))
    year_match = re.search(r"(\d{4})年", month)
    md_match = re.search(r"(\d+)月(\d+)日", date_text)
    h_match = re.search(r"(\d+)时", hour_text)
    year = int(year_match.group(1)) if year_match else 2030
    mon = int(md_match.group(1)) if md_match else 1
    day = int(md_match.group(2)) if md_match else 1
    hour = int(h_match.group(1)) if h_match else 0
    doy = sum(calendar.monthrange(year, m)[1] for m in range(1, mon)) + day
    return (doy - 1) * 24 + hour


def find_row(rows: list[dict[str, Any]], first_col: str) -> dict[str, Any]:
    for row in rows:
        if row.get("first_col") == first_col:
            return row
    return {}


def row_number(rows: list[dict[str, Any]], first_col: str, column: str, default: float = 0.0) -> float:
    return as_float(find_row(rows, first_col).get(column), default)


def max_month_pair(rows: list[dict[str, Any]], first_col: str) -> tuple[str, float]:
    row = find_row(rows, first_col)
    best = ("", 0.0)
    for key, value in row.items():
        if re.fullmatch(r"\d{4}年\d+月", str(key)):
            number = abs(as_float(value))
            if number > best[1]:
                best = (str(key), number)
    return best


def last_month(months: list[str]) -> str:
    return months[-1] if months else "2030年12月"


def normalize_util_rate(curt_rate_percent: float) -> float:
    return max(0.0, min(1.0, 1 - curt_rate_percent / 100))


def cap_value(rows: list[dict[str, Any]], label: str, key: str = "capacity_mw") -> float:
    for row in rows:
        if row.get("type") == label:
            return as_float(row.get(key))
    return 0.0


def severity(gap_mw: float, shortage_gwh: float) -> str:
    if gap_mw <= 0 and shortage_gwh <= 0:
        return "无缺口"
    if gap_mw >= 1000 or shortage_gwh >= 10:
        return "严重"
    return "轻度"


def sum_number(values: Any) -> float:
    return sum(as_float(value) for value in values)


def as_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def infer_region(name: str) -> str:
    for region in ["浙江", "江苏", "上海", "安徽", "福建", "华东", "华北", "华中", "华南", "西北", "西南", "东北"]:
        if region in name:
            return region
    return "目标区域"


def infer_year(value: Any) -> str:
    match = re.search(r"(20\d{2})", str(value or ""))
    return match.group(1) if match else "规划水平年"


def load_content(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def fmt(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 100:
        return f"{value:,.1f}"
    return f"{value:,.2f}"


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def short_month(text: str) -> str:
    match = re.search(r"(\d+)月", text)
    return f"{match.group(1)}月" if match else text[:4]


if __name__ == "__main__":
    sys.exit(main())
