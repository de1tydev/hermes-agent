#!/usr/bin/env python3
"""Multi-round duplicate issue search for the local Jira skill."""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

DEFAULT_BINARY = "jira"
DEFAULT_PROJECT = "TEAP"
DEFAULT_SERVER = "https://jira.tode.ltd"
DEFAULT_CONFIG = os.path.expanduser("~/.config/.jira/.config.yml")
GENERIC_STOPWORDS = {
    "支持",
    "新增",
    "增加",
    "优化",
    "改进",
    "修复",
    "实现",
    "处理",
    "相关",
    "需求",
    "功能",
    "问题",
    "页面",
    "接口",
    "模块",
    "逻辑",
    "流程",
    "系统",
    "当前",
    "需要",
    "进行",
    "以及",
    "包括",
    "issue",
    "jira",
    "bug",
    "feature",
    "task",
}
ACTION_MARKERS = re.compile(r"(?:以及|及|和|与|并且|并|或者|或|支持|增加|新增|优化|改进|修复|实现|兼容|指定|处理|调整|生成|允许|补充|切换|同步|升级|引入)")
SPLIT_RE = re.compile(r"[\s,，。；;、/\\|：:（）()\[\]【】<>《》\-—_\n\r\t]+")
ALNUM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{1,}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search for duplicate Jira issues with multi-round keyword queries.")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="Jira project key. Default: TEAP")
    parser.add_argument("--title", required=True, help="Proposed issue title")
    parser.add_argument("--description", help="Proposed issue description")
    parser.add_argument("--description-file", help="Read description from a file")
    parser.add_argument("--keyword", action="append", default=[], help="Extra keywords to force into duplicate search (repeatable)")
    parser.add_argument("--paginate", default="0:30", help="Per-query pagination for jira issue list --raw. Default: 0:30")
    parser.add_argument("--max-keywords", type=int, default=8, help="Maximum derived keywords to keep. Default: 8")
    parser.add_argument("--max-pairs", type=int, default=4, help="Maximum keyword-pair rounds. Default: 4")
    parser.add_argument("--binary", default=DEFAULT_BINARY, help="Path to jira CLI binary")
    parser.add_argument("--env-file", default=".env", help="Optional env file to load before running jira")
    parser.add_argument("--server", help="Jira server URL for browse links; defaults to config or local default")
    parser.add_argument("--dry-run", action="store_true", help="Print planned queries only, do not execute")
    return parser.parse_args()


def iter_env_candidates(path: str) -> List[Path]:
    raw = os.path.expanduser(path)
    candidates: List[Path] = []

    explicit = os.environ.get("JIRA_ENV_FILE")
    if explicit:
        candidates.append(Path(os.path.expanduser(explicit)))

    env_path = Path(raw)
    candidates.append(env_path)

    if not env_path.is_absolute():
        candidates.append(Path.cwd() / env_path)

    seen = set()
    ordered: List[Path] = []
    for candidate in candidates:
        resolved = str(candidate)
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(candidate)
    return ordered


def load_dotenv(path: str) -> str | None:
    for env_path in iter_env_candidates(path):
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            value = value.strip()
            if value and len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ[key] = value
        return str(env_path)
    return None



def read_description(args: argparse.Namespace) -> str:
    if args.description and args.description_file:
        raise SystemExit("Use either --description or --description-file, not both.")
    if args.description_file:
        return Path(args.description_file).read_text(encoding="utf-8")
    return args.description or ""



def normalize_text(text: str) -> str:
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text



def dedupe_keep_order(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result



def split_fragments(text: str) -> List[str]:
    fragments: List[str] = []
    for piece in SPLIT_RE.split(text):
        piece = piece.strip()
        if len(piece) < 2:
            continue
        fragments.append(piece)
        for sub in ACTION_MARKERS.split(piece):
            sub = sub.strip()
            if len(sub) >= 2 and sub != piece:
                fragments.append(sub)
    return dedupe_keep_order(fragments)



def expand_phrase(phrase: str) -> List[str]:
    phrase = normalize_text(phrase)
    if len(phrase) < 2:
        return []
    items = [phrase]
    if len(phrase) > 12:
        items.extend(sub for sub in ACTION_MARKERS.split(phrase) if len(sub.strip()) >= 2)
        items.append(phrase[:8])
        items.append(phrase[-8:])
    return dedupe_keep_order(item.strip() for item in items if 2 <= len(item.strip()) <= 18)



def score_keyword(keyword: str, manual: set[str], title_text: str, description_text: str) -> int:
    score = 0
    if keyword in manual:
        score += 100
    if keyword and keyword in title_text:
        score += 40
    if keyword and keyword in description_text:
        score += 10
    if 3 <= len(keyword) <= 10:
        score += 6
    elif 11 <= len(keyword) <= 18:
        score += 2
    if ALNUM_RE.fullmatch(keyword):
        score += 4
    return score



def extract_keywords(title: str, description: str, manual_keywords: Sequence[str], max_keywords: int) -> Tuple[List[str], List[str]]:
    title_text = normalize_text(title)
    description_text = normalize_text(description)
    manual = {normalize_text(item) for item in manual_keywords if normalize_text(item)}

    title_fragments: List[str] = []
    for fragment in split_fragments(title_text):
        title_fragments.extend(expand_phrase(fragment))
    title_fragments = dedupe_keep_order(title_fragments)

    raw_candidates: List[str] = []
    raw_candidates.extend(manual)
    raw_candidates.extend(title_fragments)

    for token in ALNUM_RE.findall(f"{title_text} {description_text}"):
        raw_candidates.append(token)

    desc_fragments = split_fragments(description_text)
    for fragment in desc_fragments[:12]:
        raw_candidates.extend(expand_phrase(fragment)[:2])

    scored: Dict[str, int] = {}
    for candidate in raw_candidates:
        candidate = normalize_text(candidate)
        if len(candidate) < 2:
            continue
        if candidate.lower() in GENERIC_STOPWORDS or candidate in GENERIC_STOPWORDS:
            continue
        if candidate.isdigit():
            continue
        scored[candidate] = max(scored.get(candidate, 0), score_keyword(candidate, manual, title_text, description_text))

    ordered = sorted(scored, key=lambda item: (-scored[item], item))
    keywords = ordered[:max_keywords]
    return title_fragments, keywords



def escape_jql_term(term: str) -> str:
    return term.replace('\\', '\\\\').replace('"', '\\"')



def make_rounds(project: str, title: str, title_fragments: Sequence[str], keywords: Sequence[str], max_pairs: int) -> List[Dict[str, str]]:
    rounds: List[Dict[str, str]] = []
    full_title = normalize_text(title)

    def add_round(name: str, purpose: str, jql: str) -> None:
        if any(existing["jql"] == jql for existing in rounds):
            return
        rounds.append({"name": name, "purpose": purpose, "jql": jql})

    if full_title:
        add_round(
            "summary-full-title",
            "Search summary with the full proposed title.",
            f'project = {project} AND summary ~ "{escape_jql_term(full_title)}"',
        )
        add_round(
            "text-full-title",
            "Search full text with the full proposed title.",
            f'project = {project} AND text ~ "{escape_jql_term(full_title)}"',
        )

    phrase_terms = [term for term in title_fragments if term != full_title][:4]
    if phrase_terms:
        joined = " OR ".join(f'summary ~ "{escape_jql_term(term)}"' for term in phrase_terms)
        add_round(
            "summary-title-fragments",
            "Search summary by title fragments to catch paraphrased duplicates.",
            f"project = {project} AND ({joined})",
        )

    strong_keywords = list(keywords[:5])
    if len(strong_keywords) >= 2:
        and_terms = " AND ".join(f'text ~ "{escape_jql_term(term)}"' for term in strong_keywords[:3])
        add_round(
            "text-core-keywords-and",
            "Search text using the strongest keywords together.",
            f"project = {project} AND {and_terms}",
        )

    pair_count = 0
    for left, right in itertools.combinations(strong_keywords[:5], 2):
        if pair_count >= max_pairs:
            break
        add_round(
            f"text-keyword-pair-{pair_count + 1}",
            f"Search text with keyword pair: {left} + {right}.",
            f'project = {project} AND text ~ "{escape_jql_term(left)}" AND text ~ "{escape_jql_term(right)}"',
        )
        pair_count += 1

    if strong_keywords:
        or_terms = []
        for term in strong_keywords:
            escaped = escape_jql_term(term)
            or_terms.append(f'summary ~ "{escaped}"')
            or_terms.append(f'text ~ "{escaped}"')
        add_round(
            "broad-keyword-or",
            "Broad fallback search across summary and description keywords.",
            f"project = {project} AND ({' OR '.join(or_terms)})",
        )

    return rounds



def run_jira_query(binary: str, project: str, paginate: str, jql: str) -> Tuple[List[dict], str | None]:
    cmd = [binary, "issue", "list", "-p", project, "--paginate", paginate, "--raw", "-q", jql]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=os.environ.copy())
    if proc.returncode != 0:
        return [], proc.stderr.strip() or proc.stdout.strip() or f"jira exited with code {proc.returncode}"
    stdout = proc.stdout.strip()
    if not stdout:
        return [], None
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return [], f"Failed to parse jira output as JSON: {exc}"
    if isinstance(parsed, list):
        return parsed, None
    if isinstance(parsed, dict) and isinstance(parsed.get("issues"), list):
        return parsed["issues"], None
    return [], "Unexpected jira JSON shape; expected a list of issues."



def read_server_from_config(path: str) -> str | None:
    config_path = Path(os.path.expanduser(path))
    if not config_path.exists():
        return None
    text = config_path.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"^\s*server:\s*(\S+)\s*$", text, flags=re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None



def summarize_issue(issue: dict, matched_round: dict, server: str) -> dict:
    fields = issue.get("fields", {})
    components = [item.get("name") for item in fields.get("components") or [] if item.get("name")]
    return {
        "key": issue.get("key"),
        "summary": fields.get("summary") or "",
        "description": fields.get("description") or "",
        "status": (fields.get("status") or {}).get("name") or "",
        "priority": (fields.get("priority") or {}).get("name") or "",
        "assignee": ((fields.get("assignee") or {}).get("displayName") or ""),
        "reporter": ((fields.get("reporter") or {}).get("displayName") or ""),
        "components": components,
        "created": fields.get("created") or "",
        "updated": fields.get("updated") or "",
        "matchedRounds": [matched_round["name"]],
        "matchedPurposes": [matched_round["purpose"]],
        "matchedQueries": [matched_round["jql"]],
        "browseUrl": f"{server.rstrip('/')}/browse/{issue.get('key')}",
    }



def compute_match_metrics(candidate: dict, title: str, keywords: Sequence[str]) -> None:
    summary = normalize_text(candidate.get("summary") or "")
    description = normalize_text(candidate.get("description") or "")
    title_norm = normalize_text(title)
    joined = f"{summary}\n{description}"
    keyword_hits = [keyword for keyword in keywords if keyword and keyword in joined]

    score = 0
    if title_norm and title_norm in summary:
        score += 80
    elif title_norm and title_norm in joined:
        score += 50
    score += len(candidate.get("matchedRounds", [])) * 12
    score += len(keyword_hits) * 5

    candidate["keywordHits"] = keyword_hits
    candidate["matchScore"] = score
    candidate["description"] = None



def main() -> int:
    args = parse_args()
    loaded_env = load_dotenv(args.env_file)
    if os.environ.get("JIRA_API_TOKEN") and not os.environ.get("JIRA_AUTH_TYPE"):
        os.environ["JIRA_AUTH_TYPE"] = "bearer"

    description = read_description(args)
    title_fragments, keywords = extract_keywords(args.title, description, args.keyword, args.max_keywords)
    rounds = make_rounds(args.project, args.title, title_fragments, keywords, args.max_pairs)
    server = args.server or os.environ.get("JIRA_SERVER") or read_server_from_config(DEFAULT_CONFIG) or DEFAULT_SERVER

    if args.dry_run:
        json.dump(
            {
                "project": args.project,
                "title": args.title,
                "titleFragments": title_fragments,
                "keywords": keywords,
                "rounds": rounds,
            },
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0

    candidates: Dict[str, dict] = {}
    round_results: List[dict] = []

    for round_info in rounds:
        issues, error = run_jira_query(args.binary, args.project, args.paginate, round_info["jql"])
        result = dict(round_info)
        result["resultCount"] = len(issues)
        if error:
            result["error"] = error
            round_results.append(result)
            continue
        round_results.append(result)
        for issue in issues:
            key = issue.get("key")
            if not key:
                continue
            if key not in candidates:
                candidates[key] = summarize_issue(issue, round_info, server)
            else:
                candidates[key]["matchedRounds"].append(round_info["name"])
                candidates[key]["matchedPurposes"].append(round_info["purpose"])
                candidates[key]["matchedQueries"].append(round_info["jql"])

    candidate_list = list(candidates.values())
    for candidate in candidate_list:
        compute_match_metrics(candidate, args.title, keywords)

    candidate_list.sort(key=lambda item: (-item["matchScore"], -(len(item.get("matchedRounds", []))), item.get("updated") or "", item.get("key") or ""))

    output = {
        "project": args.project,
        "title": args.title,
        "titleFragments": title_fragments,
        "keywords": keywords,
        "rounds": round_results,
        "summary": {
            "roundCount": len(round_results),
            "candidateCount": len(candidate_list),
            "highConfidenceCount": sum(1 for item in candidate_list if item.get("matchScore", 0) >= 70),
            "loadedEnvFile": loaded_env,
        },
        "candidates": candidate_list,
    }
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
