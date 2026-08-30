#!/usr/bin/env python3
"""逐 Profile 调用 DeepSeek v4 flash 生成 TODE-68 上下文改写草稿。"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml


SCHEMA = "tode68-profile-context-draft/v1"
MODEL = "deepseek-v4-flash"
LIMITS = {"memory": 4_000, "user": 1_200, "soul": 1_600, "agents": 1_800}
SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?P<prefix>(?:api[_ -]?key|token|secret|password|passwd|authorization)\s*[:=]\s*)"
    r"(?P<value>[^\s,;，；]+)"
)
BEARER = re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]{12,}")
SK_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
HEX_SECRET = re.compile(r"\b[0-9a-fA-F]{64,}\b")
RAW_FEISHU_ID = re.compile(r"\b(?:ou_|oc_|on_)[A-Za-z0-9_-]+")
TRANSIENT_ID_FIELD = re.compile(r"(?i)(?:session|message|job)[ _-]?id")
TRANSIENT_ID_VALUE = re.compile(
    r"(?i)(?:session|message|job)[ _-]?id\s*[:=]?\s*`?[{]?[A-Za-z0-9_-]+[}]?`?"
)
RETIRED_RUNTIME_INPUT = re.compile(
    r"(?i)(?:openclaw|access-control|Sender ID|\bOwner\b|\bmemory/)"
)
FORBIDDEN_OUTPUT = re.compile(
    r"(?i)(?:Promoted From Short-Term Memory|Conversation Summary|"
    r"(?:Deep|REM|Light) Sleep|openclaw-memory-promotion|"
    r"\bsessions_spawn\b|openclaw|access-control|Sender ID|\bOwner\b|\bmemory/|"
    r"\b(?:ou_|oc_|on_)[A-Za-z0-9_-]+|(?:session|message|job)[ _-]?id|"
    r"<(?:RETIRED|REDACTED|TRANSIENT)_"
    r")"
)

SYSTEM_PROMPT = """你是生产环境 Hermes Agent 的 Profile 上下文编辑器。你的任务是基于给定的一个 Profile 的原始文件，语义判断哪些信息仍然长期有效，并改写为精简、准确、可长期维护的四个 Markdown 文件。

当前事实：OpenClaw 已退役，当前系统是 Hermes Agent；每个聊天独立对应一个 Profile；MEMORY.md、USER.md、SOUL.md 会自动注入，无需在 AGENTS.md 中要求再次读取；历史对话按需使用 session_search，旧迁移历史按需使用 legacy-memory-search；workspace 不存在也不应创建 `memory/` 目录，原生 Memory 由 Hermes 内置 Memory 管理；安全边界由 Hermes 原生授权和 Landlock 提供；当前委托工具名是 delegate_task。旧文件里的 Owner、Sender ID、access-control 插件或“只有主人可访问本 Profile 记忆”的文字规则已经退役，不得转述或概念性保留；只保留“不跨 Profile 泄露数据”这一当前原则。

编辑要求：
1. MEMORY 只保留已确认、跨会话长期有效、对未来任务有直接帮助的事实、偏好、进行中的长期事项和稳定业务规则。删除原始聊天、Session/Sleep/Promotion、一次性结果、旧路径、旧 OpenClaw/Owner 规则、动态版本/行情、最后更新时间、已完成任务、运行日志、ID、密钥和重复内容。
2. USER 只保留稳定的用户/群体背景、专业领域和交互偏好；不要做人物档案，不保留 ID、临时状态或重复模板。
3. SOUL 保留该 Profile 独特的名称、性格、群聊角色和专属职责，同时改成当前 Hermes 术语；不要保留 sessions_spawn、OpenClaw 身份或大段通用口号。
4. AGENTS 保留真正属于该 Profile 的长期工作规则、群聊行为、表格/Jira/仿真等专属约定；删除启动时重复读取 Memory、workspace `memory/` 路由、旧插件、旧 Owner Hook、过时目录和重复原则。若原文件不存在，返回空字符串，不要凭空创建。
5. 不得编造原文不存在的个人事实、权限、业务规则、文件或能力。无法确认是否仍有效的动态信息直接删除。
6. 输出使用中文为主，命令、API、文件名和既有名称可保留英文。
7. 严格遵守字符上限：memory 4000、user 1200、soul 1600、agents 1800。宁可更短，不要为了填满而保留噪声。

只返回一个 JSON object，键必须恰好为 memory、user、soul、agents、removed_summary。前四项是完整 Markdown 字符串；removed_summary 是不超过 8 项的简短字符串数组。不要返回代码块或解释。"""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file() or path.is_symlink():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip("'\"")
    return values


def redact_for_model(text: str) -> str:
    text = BEARER.sub("Bearer <REDACTED>", text)
    text = SK_TOKEN.sub("<REDACTED>", text)
    text = SECRET_ASSIGNMENT.sub(lambda match: match.group("prefix") + "<REDACTED>", text)
    text = HEX_SECRET.sub("<REDACTED>", text)
    text = TRANSIENT_ID_VALUE.sub("", text)
    text = RAW_FEISHU_ID.sub("", text)
    text = TRANSIENT_ID_FIELD.sub("", text)
    text = RETIRED_RUNTIME_INPUT.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def profile_inputs(profile: Path) -> tuple[dict[str, str], dict[str, str | None]]:
    paths = {
        "memory": profile / "memories/MEMORY.md",
        "user": profile / "memories/USER.md",
        "soul": profile / "SOUL.md",
        "agents": profile / "workspace/AGENTS.md",
    }
    content: dict[str, str] = {}
    hashes: dict[str, str | None] = {}
    for name, path in paths.items():
        if path.is_file() and not path.is_symlink():
            raw = path.read_text(encoding="utf-8", errors="replace")
            content[name] = redact_for_model(raw)
            hashes[name] = sha256_file(path)
        else:
            content[name] = ""
            hashes[name] = None
    return content, hashes


def build_user_prompt(profile: str, content: dict[str, str]) -> str:
    payload = {
        "profile": profile,
        "profile_kind": (
            "group" if "group" in profile else "dm" if "dm" in profile else "functional"
        ),
        "source_files": content,
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_model_json(content: str) -> dict[str, Any]:
    raw = content.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model output is not an object")
    return data


def validate_rewrite(data: dict[str, Any], *, agents_existed: bool) -> dict[str, Any]:
    required = {"memory", "user", "soul", "agents", "removed_summary"}
    if set(data) != required:
        raise ValueError(f"unexpected output keys: {sorted(data)}")
    normalized: dict[str, Any] = {}
    for name, limit in LIMITS.items():
        value = data[name]
        if not isinstance(value, str):
            raise ValueError(f"{name} is not text")
        value = value.strip()
        if name == "agents" and not agents_existed and value:
            raise ValueError("model invented AGENTS.md for a Profile that had none")
        if len(value) > limit:
            raise ValueError(f"{name} exceeds {limit} chars: {len(value)}")
        forbidden = FORBIDDEN_OUTPUT.search(value)
        if forbidden:
            raise ValueError(
                f"{name} retained retired runtime artifact: {forbidden.group(0)!r}"
            )
        if BEARER.search(value) or SK_TOKEN.search(value) or SECRET_ASSIGNMENT.search(value):
            raise ValueError(f"{name} contains a possible secret")
        if name in {"memory", "user", "soul"} and not value:
            heading = {"memory": "# MEMORY.md", "user": "# USER.md", "soul": "# SOUL.md"}[name]
            value = heading
        normalized[name] = value + ("\n" if value else "")
    summary = data["removed_summary"]
    if not isinstance(summary, list) or len(summary) > 8 or not all(isinstance(v, str) for v in summary):
        raise ValueError("removed_summary must be a list of at most 8 strings")
    normalized["removed_summary"] = [value[:160] for value in summary]
    return normalized


def provider_settings(root: Path) -> tuple[str, str]:
    config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8")) or {}
    model_cfg = config.get("model") or {}
    provider_name = model_cfg.get("provider") or "tode"
    provider = (config.get("providers") or {}).get(provider_name) or {}
    base_url = str(provider.get("base_url") or "").rstrip("/")
    key_env = str(provider.get("key_env") or "OPENAI_API_KEY")
    key = parse_env(root / ".env").get(key_env) or os.environ.get(key_env, "")
    if not base_url or not key:
        raise RuntimeError("provider base_url or scoped API key is missing")
    return base_url, key


def post_chat_completion(
    base_url: str,
    api_key: str,
    user_prompt: str,
    *,
    system_prompt: str = SYSTEM_PROMPT,
    timeout: int = 300,
) -> tuple[str, dict[str, Any]]:
    request_payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    request = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"provider HTTP {exc.code}: {detail}") from exc
    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("provider response has no choices")
    content = ((choices[0].get("message") or {}).get("content"))
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("provider response has no text content")
    return content, body.get("usage") or {}


def generate_profile_draft(
    root: Path,
    profile: Path,
    call: Callable[[str, str, str], tuple[str, dict[str, Any]]],
    *,
    attempts: int = 2,
) -> dict[str, Any]:
    content, hashes = profile_inputs(profile)
    user_prompt = build_user_prompt(profile.name, content)
    errors = []
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            raw, usage = call(profile.name, SYSTEM_PROMPT, user_prompt)
            output = validate_rewrite(
                parse_model_json(raw),
                agents_existed=hashes["agents"] is not None,
            )
            return {
                "schema_version": SCHEMA,
                "profile": profile.name,
                "model": MODEL,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "input_hashes": hashes,
                "output": output,
                "usage": {
                    "prompt_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
                    "total_tokens": int(usage.get("total_tokens") or 0),
                },
                "latency_seconds": round(time.monotonic() - started, 3),
                "attempt": attempt,
            }
        except Exception as exc:
            errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
            print(
                f"[{profile.name}] attempt {attempt} failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
    raise RuntimeError(f"{profile.name} rewrite failed: {'; '.join(errors)}")


def generate_profile_draft_split(
    profile: Path,
    call: Callable[[str, str, str], tuple[str, dict[str, Any]]],
    *,
    attempts: int = 2,
) -> dict[str, Any]:
    content, hashes = profile_inputs(profile)
    output: dict[str, Any] = {}
    summaries: list[str] = []
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    started_all = time.monotonic()
    max_attempt = 1
    for name, limit in LIMITS.items():
        if name == "agents" and hashes["agents"] is None:
            output[name] = ""
            continue
        system_prompt = SYSTEM_PROMPT + (
            f"\n\n本次只改写 `{name}` 一个文件。只返回 JSON object："
            '{"content":"完整 Markdown","removed_summary":["删除类别"]}。'
            f"content 不得超过 {limit} 字符；不要返回其他文件。"
        )
        if name == "memory":
            system_prompt += (
                " 不得写出 `job_id`、`session_id`、`message_id` 等接口字段名，"
                "也不要列出含这些字段的 API 路径模板；若能力本身长期有效，只概括能力和使用场景。"
            )
        user_prompt = json.dumps(
            {
                "profile": profile.name,
                "profile_kind": (
                    "group" if "group" in profile.name else "dm" if "dm" in profile.name else "functional"
                ),
                "file_type": name,
                "source": content[name],
            },
            ensure_ascii=False,
        )
        errors = []
        for attempt in range(1, attempts + 1):
            try:
                raw, usage = call(profile.name, system_prompt, user_prompt)
                data = parse_model_json(raw)
                if set(data) != {"content", "removed_summary"}:
                    raise ValueError(f"unexpected split output keys: {sorted(data)}")
                candidate = validate_rewrite(
                    {
                        "memory": data["content"] if name == "memory" else "# MEMORY.md",
                        "user": data["content"] if name == "user" else "# USER.md",
                        "soul": data["content"] if name == "soul" else "# SOUL.md",
                        "agents": data["content"] if name == "agents" else "",
                        "removed_summary": data["removed_summary"],
                    },
                    agents_existed=name == "agents",
                )[name]
                output[name] = candidate
                summaries.extend(str(value) for value in data["removed_summary"])
                total_usage["prompt_tokens"] += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
                total_usage["completion_tokens"] += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
                total_usage["total_tokens"] += int(usage.get("total_tokens") or 0)
                max_attempt = max(max_attempt, attempt)
                break
            except Exception as exc:
                errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
                print(
                    f"[{profile.name}/{name}] attempt {attempt} failed: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
        else:
            raise RuntimeError(f"{profile.name}/{name} split rewrite failed: {'; '.join(errors)}")
    output["removed_summary"] = list(dict.fromkeys(summaries))[:8]
    return {
        "schema_version": SCHEMA,
        "profile": profile.name,
        "model": MODEL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_hashes": hashes,
        "output": output,
        "usage": total_usage,
        "latency_seconds": round(time.monotonic() - started_all, 3),
        "attempt": max_attempt,
        "generation_mode": "split-files",
    }


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.draft-", dir=path.parent)
    tmp = Path(raw)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--split-files", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    output_dir = args.output_dir.resolve(strict=False)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    base_url, api_key = provider_settings(root)

    selected = set(args.profile)
    profiles = sorted(path for path in (root / "profiles").iterdir() if path.is_dir())
    if selected:
        profiles = [path for path in profiles if path.name in selected]
        missing = selected - {path.name for path in profiles}
        if missing:
            raise RuntimeError(f"unknown Profile(s): {sorted(missing)}")

    def call(_profile: str, system_prompt: str, user_prompt: str) -> tuple[str, dict[str, Any]]:
        return post_chat_completion(
            base_url,
            api_key,
            user_prompt,
            system_prompt=system_prompt,
        )

    results = []
    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as executor:
        futures = {
            (
                executor.submit(generate_profile_draft_split, profile, call)
                if args.split_files
                else executor.submit(generate_profile_draft, root, profile, call)
            ): profile
            for profile in profiles
        }
        for future in concurrent.futures.as_completed(futures):
            profile = futures[future]
            try:
                draft = future.result()
            except Exception as exc:
                failure = {
                    "profile": profile.name,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                failures.append(failure)
                print(
                    json.dumps({"event": "draft_failed", **failure}, ensure_ascii=False),
                    flush=True,
                )
                continue
            atomic_json(output_dir / f"{profile.name}.json", draft)
            results.append(
                {
                    "profile": profile.name,
                    "usage": draft["usage"],
                    "latency_seconds": draft["latency_seconds"],
                    "attempt": draft["attempt"],
                    "output_chars": {
                        key: len(draft["output"][key])
                        for key in ("memory", "user", "soul", "agents")
                    },
                }
            )
            print(
                json.dumps(
                    {
                        "event": "draft_complete",
                        "profile": profile.name,
                        "attempt": draft["attempt"],
                        "latency_seconds": draft["latency_seconds"],
                        "usage": draft["usage"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    summary = {
        "schema_version": SCHEMA,
        "model": MODEL,
        "profiles": sorted(results, key=lambda row: row["profile"]),
        "totals": {
            "profiles": len(results),
            "prompt_tokens": sum(row["usage"]["prompt_tokens"] for row in results),
            "completion_tokens": sum(row["usage"]["completion_tokens"] for row in results),
            "total_tokens": sum(row["usage"]["total_tokens"] for row in results),
        },
        "failures": sorted(failures, key=lambda row: row["profile"]),
    }
    atomic_json(output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
