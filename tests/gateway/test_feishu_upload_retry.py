"""Feishu SDK timeout propagation and resilient file-upload retries."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from requests.exceptions import ReadTimeout

from gateway.config import PlatformConfig
from plugins.platforms.feishu import adapter as feishu_module
from plugins.platforms.feishu.adapter import FeishuAdapter


def test_feishu_http_timeout_is_applied_to_sdk_client(monkeypatch):
    calls: list[tuple[str, object]] = []

    class _Builder:
        def app_id(self, value):
            calls.append(("app_id", value))
            return self

        def app_secret(self, value):
            calls.append(("app_secret", value))
            return self

        def domain(self, value):
            calls.append(("domain", value))
            return self

        def log_level(self, value):
            calls.append(("log_level", value))
            return self

        def timeout(self, value):
            calls.append(("timeout", value))
            return self

        def build(self):
            return "client"

    builder = _Builder()
    monkeypatch.setattr(
        feishu_module,
        "lark",
        SimpleNamespace(
            Client=SimpleNamespace(builder=lambda: builder),
            LogLevel=SimpleNamespace(WARNING="warning"),
        ),
    )
    adapter = FeishuAdapter(
        PlatformConfig(extra={"http_timeout_seconds": 180})
    )

    assert adapter._build_lark_client("feishu-domain") == "client"
    assert ("timeout", 180.0) in calls


def test_file_upload_retries_timeout_and_reopens_file(tmp_path: Path):
    payload = b"retryable upload payload"
    upload_path = tmp_path / "artifact.zip"
    upload_path.write_bytes(payload)
    uploaded_payloads: list[bytes] = []

    class _FileAPI:
        def create(self, request):
            uploaded_payloads.append(request["file"].read())
            if len(uploaded_payloads) == 1:
                raise ReadTimeout("upload timed out")
            return SimpleNamespace(
                success=lambda: True,
                data=SimpleNamespace(file_key="file-key"),
            )

    adapter = FeishuAdapter(
        PlatformConfig(extra={"file_upload_attempts": 2})
    )
    adapter._client = SimpleNamespace(
        im=SimpleNamespace(v1=SimpleNamespace(file=_FileAPI()))
    )
    adapter._build_file_upload_body = lambda **kwargs: kwargs
    adapter._build_file_upload_request = lambda body: body
    adapter._feishu_send_with_retry = AsyncMock(
        return_value=SimpleNamespace(
            success=lambda: True,
            data=SimpleNamespace(message_id="message-id"),
        )
    )

    async def _direct(func, *args, **kwargs):
        return func(*args, **kwargs)

    adapter._run_blocking = _direct

    result = asyncio.run(
        adapter._send_uploaded_file_message(
            chat_id="oc_chat",
            file_path=str(upload_path),
            reply_to=None,
            metadata=None,
        )
    )

    assert result.success is True
    assert uploaded_payloads == [payload, payload]


def test_file_upload_does_not_retry_api_rejection(tmp_path: Path):
    upload_path = tmp_path / "artifact.zip"
    upload_path.write_bytes(b"payload")
    upload_calls = 0

    class _FileAPI:
        def create(self, request):
            nonlocal upload_calls
            upload_calls += 1
            request["file"].read()
            return SimpleNamespace(
                success=lambda: False,
                code=234001,
                msg="file too large",
                data=None,
            )

    adapter = FeishuAdapter(
        PlatformConfig(extra={"file_upload_attempts": 3})
    )
    adapter._client = SimpleNamespace(
        im=SimpleNamespace(v1=SimpleNamespace(file=_FileAPI()))
    )
    adapter._build_file_upload_body = lambda **kwargs: kwargs
    adapter._build_file_upload_request = lambda body: body

    async def _direct(func, *args, **kwargs):
        return func(*args, **kwargs)

    adapter._run_blocking = _direct

    result = asyncio.run(
        adapter._send_uploaded_file_message(
            chat_id="oc_chat",
            file_path=str(upload_path),
            reply_to=None,
            metadata=None,
        )
    )

    assert result.success is False
    assert "missing file_key" in (result.error or "")
    assert upload_calls == 1
