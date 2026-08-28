import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType
from gateway.profile_routing import ProfileRoute
from gateway.run import GatewayRunner
from gateway.session import Platform, SessionSource, build_session_key
from plugins.platforms.feishu.adapter import FeishuAdapter


class _StubAdapter(BasePlatformAdapter):
    async def connect(self):
        return True

    async def disconnect(self):
        return None

    async def send(self, chat_id, content, **kwargs):
        raise AssertionError("no send expected")

    async def get_chat_info(self, chat_id):
        return {"name": chat_id, "type": "dm"}


def _event(message_type=MessageType.TEXT):
    return MessageEvent(
        text="hello",
        message_type=message_type,
        source=SessionSource(
            platform=Platform.FEISHU,
            chat_id="oc_chat",
            chat_type="dm",
            user_id="ou_user",
        ),
        media_urls=["/tmp/input.png"] if message_type == MessageType.PHOTO else [],
    )


def _group_routing_runner():
    runner = object.__new__(GatewayRunner)
    routes = [
        ProfileRoute(
            name="dm-alice",
            platform="feishu",
            profile="dm-alice",
            user_id="ou_alice",
        ),
        ProfileRoute(
            name="group-ops",
            platform="feishu",
            profile="group-ops",
            chat_id="oc_ops",
        ),
    ]
    routes.sort(key=lambda route: route.specificity, reverse=True)
    runner.config = SimpleNamespace(
        multiplex_profiles=True,
        multiplex_profile_allowlist=None,
        profile_routes=routes,
    )
    return runner


def _build_group_source(adapter):
    with patch(
        "hermes_cli.profiles.profiles_to_serve",
        return_value=[
            ("default", Path("/profiles/default")),
            ("dm-alice", Path("/profiles/dm-alice")),
            ("group-ops", Path("/profiles/group-ops")),
        ],
    ):
        return adapter.build_source(
            chat_id="oc_ops",
            chat_name="Ops",
            chat_type="group",
            user_id="ou_alice",
            user_name="Alice",
        )


@pytest.mark.asyncio
async def test_base_rejects_before_busy_or_session_key_side_effects():
    adapter = _StubAdapter(SimpleNamespace(extra={}), Platform.FEISHU)
    handled = []
    adapter.set_message_handler(lambda event: handled.append(event))

    async def reject(_source):
        return None

    adapter.set_inbound_source_preparer(reject)
    event = _event()

    await adapter.handle_message(event)

    assert handled == []
    assert adapter._active_sessions == {}
    assert adapter._pending_messages == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("message_type", [MessageType.TEXT, MessageType.PHOTO])
async def test_feishu_prepares_before_text_or_media_batch_key(message_type):
    adapter = FeishuAdapter.__new__(FeishuAdapter)
    adapter.config = SimpleNamespace(
        extra={
            "group_sessions_per_user": True,
            "thread_sessions_per_user": False,
        }
    )
    adapter._owner_profile = None
    adapter._session_store = None
    adapter.platform = Platform.FEISHU
    adapter.gateway_runner = _group_routing_runner()
    calls = []

    async def prepare(source):
        calls.append("prepare")
        return source

    adapter._inbound_source_preparer = prepare
    event = _event(message_type)
    event.source = _build_group_source(adapter)

    if message_type == MessageType.TEXT:
        async def enqueue(candidate):
            calls.append(adapter._text_batch_key(candidate))

        adapter._enqueue_text_event = enqueue
    else:
        async def enqueue(candidate):
            calls.append(adapter._media_batch_key(candidate))

        adapter._enqueue_media_event = enqueue

    await FeishuAdapter._dispatch_inbound_event(adapter, event)

    assert calls[0] == "prepare"
    assert calls[1].startswith("agent:group-ops:")


@pytest.mark.asyncio
async def test_feishu_and_base_prepare_is_idempotent_for_same_source():
    adapter = _StubAdapter(SimpleNamespace(extra={}), Platform.FEISHU)
    calls = 0
    handled = asyncio.Event()
    observed_keys = []

    async def prepare(source):
        nonlocal calls
        calls += 1
        source.profile = "prepared-profile"
        return source

    async def handler(candidate):
        adapter_key = next(iter(adapter._active_sessions))
        persistence_key = build_session_key(
            candidate.source,
            group_sessions_per_user=True,
            thread_sessions_per_user=False,
            profile=candidate.source.profile,
        )
        observed_keys.append((adapter_key, persistence_key))
        handled.set()

    adapter.set_inbound_source_preparer(prepare)
    adapter.set_message_handler(handler)
    event = _event()

    assert await adapter.prepare_inbound_source(event.source) is event.source
    await adapter.handle_message(event)
    await asyncio.wait_for(handled.wait(), timeout=1)
    await asyncio.gather(*adapter._session_tasks.values(), return_exceptions=True)

    assert calls == 1
    assert len(observed_keys) == 1
    adapter_key, persistence_key = observed_keys[0]
    assert adapter_key == persistence_key
    assert adapter_key.startswith("agent:prepared-profile:")


@pytest.mark.asyncio
async def test_unauthorized_feishu_media_is_rejected_before_content_extraction():
    adapter = FeishuAdapter.__new__(FeishuAdapter)
    source = _event(MessageType.PHOTO).source
    adapter.get_chat_info = AsyncMock(return_value={"name": "DM", "type": "dm"})
    adapter._resolve_sender_profile = AsyncMock(
        return_value={
            "user_id": "ou_user",
            "user_name": "User",
            "user_id_alt": None,
        }
    )
    adapter.build_source = lambda **_kwargs: source
    adapter.prepare_inbound_source = AsyncMock(return_value=None)
    adapter._extract_message_content = AsyncMock(
        side_effect=AssertionError("unauthorized media must not be downloaded")
    )

    await FeishuAdapter._process_inbound_message(
        adapter,
        data=object(),
        message=SimpleNamespace(chat_id="oc_chat", thread_id=None, root_id=None),
        sender_id=SimpleNamespace(open_id="ou_user"),
        chat_type="p2p",
        message_id="om_message",
    )

    adapter._extract_message_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_feishu_group_reaction_uses_chat_route_not_dm_user_route():
    adapter = FeishuAdapter.__new__(FeishuAdapter)
    adapter.platform = Platform.FEISHU
    adapter.gateway_runner = _group_routing_runner()
    adapter.config = SimpleNamespace(extra={})
    adapter._app_id = "cli_app"
    message_get = object()
    adapter._client = SimpleNamespace(
        im=SimpleNamespace(v1=SimpleNamespace(message=SimpleNamespace(get=message_get)))
    )
    adapter._build_get_message_request = lambda _message_id: object()
    adapter._run_blocking = AsyncMock(
        return_value=SimpleNamespace(
            success=lambda: True,
            data=SimpleNamespace(
                items=[
                    SimpleNamespace(
                        sender=SimpleNamespace(id="cli_app"),
                        chat_id="oc_ops",
                        chat_type="group",
                    )
                ]
            ),
        )
    )
    adapter._resolve_sender_profile = AsyncMock(
        return_value={
            "user_id": "ou_alice",
            "user_name": "Alice",
            "user_id_alt": None,
        }
    )
    adapter.get_chat_info = AsyncMock(return_value={"name": "Ops", "type": "group"})
    adapter._resolve_source_chat_type = lambda **_kwargs: "group"
    adapter._resolve_channel_prompt = lambda *_args: None
    adapter._handle_message_with_guards = AsyncMock()
    data = SimpleNamespace(
        event=SimpleNamespace(
            message_id="om_bot_message",
            user_id=SimpleNamespace(open_id="ou_alice"),
            reaction_type=SimpleNamespace(emoji_type="THUMBSUP"),
        )
    )

    with patch(
        "hermes_cli.profiles.profiles_to_serve",
        return_value=[
            ("default", Path("/profiles/default")),
            ("dm-alice", Path("/profiles/dm-alice")),
            ("group-ops", Path("/profiles/group-ops")),
        ],
    ):
        await FeishuAdapter._handle_reaction_event(
            adapter, "im.message.reaction.created_v1", data
        )

    routed_event = adapter._handle_message_with_guards.await_args.args[0]
    assert routed_event.source.chat_type == "group"
    assert routed_event.source.profile == "group-ops"


@pytest.mark.asyncio
async def test_feishu_group_card_action_uses_chat_route_not_dm_user_route():
    adapter = FeishuAdapter.__new__(FeishuAdapter)
    adapter.platform = Platform.FEISHU
    adapter.gateway_runner = _group_routing_runner()
    adapter.config = SimpleNamespace(extra={})
    adapter._card_action_tokens = {}
    adapter._resolve_sender_profile = AsyncMock(
        return_value={
            "user_id": "ou_alice",
            "user_name": "Alice",
            "user_id_alt": None,
        }
    )
    adapter.get_chat_info = AsyncMock(return_value={"name": "Ops", "type": "group"})
    adapter._resolve_source_chat_type = lambda **_kwargs: "group"
    adapter._resolve_channel_prompt = lambda *_args: None
    adapter._handle_message_with_guards = AsyncMock()
    data = SimpleNamespace(
        event=SimpleNamespace(
            token="card-token",
            context=SimpleNamespace(open_chat_id="oc_ops"),
            operator=SimpleNamespace(open_id="ou_alice"),
            action=SimpleNamespace(tag="button", value={"choice": "approve"}),
        )
    )

    with patch(
        "hermes_cli.profiles.profiles_to_serve",
        return_value=[
            ("default", Path("/profiles/default")),
            ("dm-alice", Path("/profiles/dm-alice")),
            ("group-ops", Path("/profiles/group-ops")),
        ],
    ):
        await FeishuAdapter._handle_card_action_event(adapter, data)

    routed_event = adapter._handle_message_with_guards.await_args.args[0]
    assert routed_event.source.chat_type == "group"
    assert routed_event.source.profile == "group-ops"
