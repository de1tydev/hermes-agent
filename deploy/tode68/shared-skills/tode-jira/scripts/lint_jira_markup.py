#!/usr/bin/env python3
"""Lint Jira Wiki Markup bodies for the local TODE Jira workflow.

This checker is intentionally opinionated for jira.tode.ltd:
- Ban leading `#` lines in issue/comment bodies because this Jira renderer may render
  them as headings instead of ordered lists.
- Ban `h1.` headings in normal issue bodies; keep section headers at `h2.` or `h3.`.
- Catch concatenated heading markers like `h2. 标题h1. 子项` caused by missing newlines.
- Warn on common LaTeX / MathJax syntax outside `{code}` / `{noformat}` blocks;
  Jira Wiki Markup does not render those reliably in issue bodies.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

HEADING_RE = re.compile(r"^h([1-6])\.\s+")
INLINE_HEADING_RE = re.compile(r"(?<!^)h([1-6])\.\s+")
ORDERED_LIST_RE = re.compile(r"^#+\s+")
VERBATIM_BLOCK_RE = re.compile(r"^\{(code|noformat)(?::[^}]*)?\}$", re.IGNORECASE)
LATEX_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\$[^\n]+\$"), "found inline '$...$' LaTeX math; rewrite as ASCII and place it in {code}"),
    (re.compile(r"\\\(|\\\)|\\\[|\\\]"), "found LaTeX math delimiters like \\( / \\[ ; rewrite as ASCII and place it in {code}"),
    (re.compile(r"\\(frac|sum|min|max|cdot|times|left|right|begin|end|alpha|beta|gamma|tau|lambda|mu|theta|partial|mathrm)\b"), "found LaTeX command; rewrite as ASCII and place it in {code}"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lint Jira Wiki Markup bodies for jira.tode.ltd")
    parser.add_argument("path", help="Path to .jira body file")
    parser.add_argument("--allow-h1", action="store_true", help="Allow h1. headings (disabled by default)")
    return parser.parse_args()


def lint_text(text: str, allow_h1: bool = False) -> list[str]:
    errors: list[str] = []
    lines = text.splitlines()
    in_verbatim_block = False

    for idx, raw in enumerate(lines, 1):
        line = raw.rstrip("\n")
        stripped = line.strip()

        if VERBATIM_BLOCK_RE.match(stripped):
            in_verbatim_block = not in_verbatim_block
            continue

        if not stripped:
            continue

        if ORDERED_LIST_RE.match(stripped):
            errors.append(
                f"Line {idx}: ban leading '#'. On jira.tode.ltd it may render as a heading; use '*' bullets instead."
            )

        heading_match = HEADING_RE.match(stripped)
        if heading_match:
            level = int(heading_match.group(1))
            if level == 1 and not allow_h1:
                errors.append(
                    f"Line {idx}: ban 'h1.' in normal issue bodies; use 'h2.' for sections and '*' bullets for items."
                )

        inline_heading_match = INLINE_HEADING_RE.search(stripped)
        if inline_heading_match:
            errors.append(
                f"Line {idx}: found inline heading marker '{inline_heading_match.group(0).strip()}'; headings must start on a new line."
            )

        if in_verbatim_block:
            continue

        for pattern, message in LATEX_PATTERNS:
            if pattern.search(stripped):
                errors.append(f"Line {idx}: {message}.")
                break

    return errors


def main() -> int:
    args = parse_args()
    path = Path(args.path)
    text = path.read_text(encoding="utf-8")
    errors = lint_text(text, allow_h1=args.allow_h1)
    if errors:
        print(f"[FAIL] Jira markup lint failed: {path}")
        for item in errors:
            print(f"- {item}")
        return 1
    print(f"[OK] Jira markup lint passed: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
