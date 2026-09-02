"""Feishu reply metadata must not silently split ordinary chat sessions."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageType
from gateway.session import build_session_key
from plugins.platforms.feishu.adapter import FeishuAdapter


def _adapter(*, chat_type: str) -> FeishuAdapter:
    adapter = FeishuAdapter(PlatformConfig(extra={"group_sessions_per_user": False}))
    adapter.get_chat_info = AsyncMock(
        return_value={
            "chat_id": "oc_teap",
            "name": "TEAP能源分析平台攻关小组",
            "type": chat_type,
        }
    )
    adapter._resolve_sender_profile = AsyncMock(
        return_value={
            "user_id": "user-a",
            "user_name": "Alice",
            "user_id_alt": None,
        }
    )
    adapter._extract_message_content = AsyncMock(
        return_value=("进展如何了？", MessageType.TEXT, [], [], [])
    )
    adapter._fetch_message_text = AsyncMock(return_value="[Attachment: case.tc]")
    adapter._fetch_message_normalized = AsyncMock(return_value=None)
    adapter._dispatch_inbound_event = AsyncMock()
    return adapter


def _inbound(
    adapter: FeishuAdapter,
    *,
    message_id: str,
    root_id: str | None = None,
    parent_id: str | None = None,
    thread_id: str | None = None,
):
    message = SimpleNamespace(
        chat_id="oc_teap",
        thread_id=thread_id,
        root_id=root_id,
        parent_id=parent_id,
        upper_message_id=None,
    )
    asyncio.run(
        adapter._process_inbound_message(
            data=message,
            message=message,
            sender_id=SimpleNamespace(
                open_id="ou_user",
                user_id="user-a",
                union_id=None,
            ),
            chat_type="group",
            message_id=message_id,
        )
    )
    return adapter._dispatch_inbound_event.await_args.args[0]


def _key(event) -> str:
    return build_session_key(
        event.source,
        group_sessions_per_user=False,
        thread_sessions_per_user=False,
    )


def test_ordinary_group_reply_keeps_shared_chat_session() -> None:
    """A quoted attachment reply and a top-level follow-up share one lane."""
    adapter = _adapter(chat_type="group")

    replied = _inbound(
        adapter,
        message_id="om_debug",
        root_id="om_attachment",
        parent_id="om_attachment",
    )
    top_level = _inbound(adapter, message_id="om_progress")

    assert replied.source.thread_id is None
    assert replied.reply_to_message_id == "om_attachment"
    assert replied.reply_to_text == "[Attachment: case.tc]"
    assert _key(replied) == _key(top_level)
    assert _key(replied) == "agent:main:feishu:group:oc_teap"


def test_forum_root_still_scopes_a_real_topic_session() -> None:
    """Topic/forum fallback keeps root_id when Feishu omits thread_id."""
    adapter = _adapter(chat_type="forum")

    event = _inbound(
        adapter,
        message_id="om_topic_reply",
        root_id="omt_topic",
        parent_id="om_topic_parent",
    )

    assert event.source.thread_id == "omt_topic"
    assert _key(event) == "agent:main:feishu:forum:oc_teap:omt_topic"


def test_explicit_thread_id_always_wins() -> None:
    adapter = _adapter(chat_type="group")

    event = _inbound(
        adapter,
        message_id="om_thread_reply",
        root_id="om_root",
        parent_id="om_parent",
        thread_id="omt_explicit",
    )

    assert event.source.thread_id == "omt_explicit"
    assert _key(event) == "agent:main:feishu:group:oc_teap:omt_explicit"


def test_topic_chat_mode_is_recognized_independently_of_visibility_type() -> None:
    """Feishu exposes topic mode separately from private/public chat type."""
    assert FeishuAdapter._map_chat_type("private", "TOPIC") == "forum"
    assert FeishuAdapter._map_chat_type("private", "DEFAULT") == "dm"
