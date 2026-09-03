from agent.auxiliary_client import _build_call_kwargs
from agent.delegation_context import delegated_child_context
from agent.portal_tags import reset_conversation_context, set_conversation_context
from agent.transports.chat_completions import ChatCompletionsTransport
from deploy.tode68.model_providers.tode import (
    _CONVERSATION_HEADER,
    _LANE_HEADER,
    build_affinity_headers,
    tode,
)


NEWAPI_URL = "https://newapi.tode.ltd/v1"


def _with_conversation(conversation_id: str):
    return set_conversation_context(conversation_id)


def test_main_requests_keep_one_conversation_id_and_separate_sessions():
    transport = ChatCompletionsTransport()

    first_token = _with_conversation("session-root-a")
    try:
        first = transport.build_kwargs(
            "deepseek-v4-flash",
            [{"role": "user", "content": "hello"}],
            provider_profile=tode,
            base_url=NEWAPI_URL,
            session_id="session-segment-a",
        )["extra_headers"]
        retry = transport.build_kwargs(
            "deepseek-v4-flash",
            [{"role": "user", "content": "hello"}],
            provider_profile=tode,
            base_url=NEWAPI_URL,
            session_id="session-segment-a",
        )["extra_headers"]
    finally:
        reset_conversation_context(first_token)

    second_token = _with_conversation("session-root-b")
    try:
        second = transport.build_kwargs(
            "deepseek-v4-flash",
            [{"role": "user", "content": "hello"}],
            provider_profile=tode,
            base_url=NEWAPI_URL,
            session_id="session-segment-b",
        )["extra_headers"]
    finally:
        reset_conversation_context(second_token)

    assert first == retry
    assert first[_CONVERSATION_HEADER] != second[_CONVERSATION_HEADER]
    assert _LANE_HEADER not in first
    assert len(first[_CONVERSATION_HEADER].encode("utf-8")) <= 256
    assert "session-root-a" not in first[_CONVERSATION_HEADER]


def test_compression_segment_reuses_the_conversation_root():
    token = _with_conversation("session-root")
    try:
        before = build_affinity_headers(
            base_url=NEWAPI_URL,
            session_id="session-root",
        )
        after = build_affinity_headers(
            base_url=NEWAPI_URL,
            session_id="session-after-compression",
        )
    finally:
        reset_conversation_context(token)

    assert before == after


def test_delegated_children_share_conversation_and_use_distinct_lanes():
    token = _with_conversation("parent-session-root")
    try:
        with delegated_child_context("child-a"):
            child_a = build_affinity_headers(
                base_url=NEWAPI_URL,
                session_id="child-a",
            )
        with delegated_child_context("child-b"):
            child_b = build_affinity_headers(
                base_url=NEWAPI_URL,
                session_id="child-b",
            )
    finally:
        reset_conversation_context(token)

    assert child_a[_CONVERSATION_HEADER] == child_b[_CONVERSATION_HEADER]
    assert child_a[_LANE_HEADER] != child_b[_LANE_HEADER]
    assert len(child_a[_LANE_HEADER].encode("utf-8")) <= 256


def test_auxiliary_requests_inherit_the_conversation_id():
    token = _with_conversation("session-root")
    try:
        kwargs = _build_call_kwargs(
            "tode",
            "deepseek-v4-flash",
            [{"role": "user", "content": "summarize"}],
            base_url=NEWAPI_URL,
        )
    finally:
        reset_conversation_context(token)

    assert _CONVERSATION_HEADER in kwargs["extra_headers"]
    assert _LANE_HEADER not in kwargs["extra_headers"]


def test_headers_are_not_sent_to_an_unrelated_endpoint():
    token = _with_conversation("session-root")
    try:
        headers = build_affinity_headers(
            base_url="https://api.openai.com/v1",
            session_id="session-root",
        )
    finally:
        reset_conversation_context(token)

    assert headers == {}
