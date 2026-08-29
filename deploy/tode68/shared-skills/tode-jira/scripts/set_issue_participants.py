#!/usr/bin/env python3
"""Set Jira issue participants for jira.tode.ltd.

Current instance note:
- 参与人 is customfield_10203 (multi-user picker)
- This is NOT the same as watchers
- Bearer token auth with the robot account can edit this field when editmeta allows it
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from typing import Any

BASE = os.environ.get("JIRA_SERVER", "https://jira.tode.ltd")
PARTICIPANT_FIELD = os.environ.get("JIRA_PARTICIPANT_FIELD", "customfield_10203")


def headers() -> dict[str, str]:
    token = os.environ.get("JIRA_API_TOKEN")
    if not token:
        raise SystemExit("JIRA_API_TOKEN is required")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def get_json(path: str) -> Any:
    req = urllib.request.Request(BASE.rstrip("/") + path, headers=headers())
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def put_json(path: str, payload: dict[str, Any]) -> int:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE.rstrip("/") + path, data=data, method="PUT", headers=headers())
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def search_user(name: str) -> dict[str, Any]:
    data = get_json("/rest/api/2/user/search?username=" + urllib.parse.quote(name))
    exact = [u for u in data if u.get("displayName") == name and u.get("active")]
    if len(exact) != 1:
        short = [{"name": u.get("name"), "displayName": u.get("displayName"), "active": u.get("active")} for u in data]
        raise SystemExit(json.dumps({
            "error": f"user search for {name} is not unique",
            "matches": short,
        }, ensure_ascii=False, indent=2))
    return exact[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Set Jira issue participants by display names")
    parser.add_argument("issue", help="Issue key, e.g. TEAP-2026")
    parser.add_argument("names", nargs="+", help="Participant display names")
    parser.add_argument("--mode", choices=["set", "add"], default="set", help="Replace or append participants")
    args = parser.parse_args()

    editmeta = get_json(f"/rest/api/2/issue/{args.issue}/editmeta")
    if PARTICIPANT_FIELD not in (editmeta.get("fields") or {}):
        raise SystemExit(json.dumps({
            "error": "participant field is not editable",
            "issue": args.issue,
            "field": PARTICIPANT_FIELD,
        }, ensure_ascii=False, indent=2))

    users = [search_user(name) for name in args.names]
    values = [{"name": u["name"]} for u in users]

    if args.mode == "set":
        payload = {"update": {PARTICIPANT_FIELD: [{"set": values}]}}
    else:
        payload = {"update": {PARTICIPANT_FIELD: [{"add": {"name": u["name"]}} for u in users]}}

    status = put_json(f"/rest/api/2/issue/{args.issue}", payload)
    issue = get_json(f"/rest/api/2/issue/{args.issue}")
    current = issue.get("fields", {}).get(PARTICIPANT_FIELD) or []
    result = {
        "status": status,
        "issue": args.issue,
        "field": PARTICIPANT_FIELD,
        "participants": [{"name": u.get("name"), "displayName": u.get("displayName")} for u in current],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
