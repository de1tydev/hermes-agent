#!/usr/bin/env python3
"""Generate an image through OpenRouter's dedicated Images API."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt")
    parser.add_argument("output")
    parser.add_argument("--model", default="google/gemini-3.1-flash-image-preview")
    parser.add_argument("--aspect-ratio", default="1:1")
    parser.add_argument("--resolution", choices=("1K", "2K", "4K"))
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        print("OPENROUTER_API_KEY is not configured", file=sys.stderr)
        return 2

    payload = {
        "model": args.model,
        "prompt": args.prompt,
        "aspect_ratio": args.aspect_ratio,
        "output_format": "png",
    }
    if args.resolution:
        payload["resolution"] = args.resolution
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/images",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://hermes-agent.nousresearch.com",
            "X-Title": "Hermes Agent",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")[:1000]
        print(f"OpenRouter HTTP {exc.code}: {message}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"OpenRouter request failed: {exc}", file=sys.stderr)
        return 1

    images = result.get("data") or []
    if not images or not images[0].get("b64_json"):
        print("OpenRouter returned no image data", file=sys.stderr)
        return 1
    try:
        image = base64.b64decode(images[0]["b64_json"], validate=True)
    except (ValueError, TypeError) as exc:
        print(f"Invalid image payload: {exc}", file=sys.stderr)
        return 1

    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(image)
    print(f"IMAGE_PATH: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
