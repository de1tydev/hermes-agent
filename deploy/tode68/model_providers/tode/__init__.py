"""TODE NewAPI provider profile for the TODE68 Hermes deployment."""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlparse

from agent.delegation_context import is_delegated_child_context
from agent.portal_tags import get_conversation_context
from providers import register_provider
from providers.base import ProviderProfile


_NEWAPI_HOST = "newapi.tode.ltd"
_CONVERSATION_HEADER = "X-NewAPI-Affinity-Conversation"
_LANE_HEADER = "X-NewAPI-Affinity-Lane"


def _is_tode_newapi(base_url: Any) -> bool:
    try:
        return (urlparse(str(base_url or "")).hostname or "").lower() == _NEWAPI_HOST
    except (TypeError, ValueError):
        return False


def _opaque_affinity_id(kind: str, value: Any) -> str:
    """Return a stable, non-sensitive identifier within NewAPI's 256-byte cap."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    digest = hashlib.sha256(
        f"hermes-tode-newapi-affinity-v1\0{kind}\0{raw}".encode("utf-8")
    ).hexdigest()
    return f"hermes-{kind}-{digest}"


def build_affinity_headers(
    *,
    base_url: Any,
    session_id: str | None = None,
) -> dict[str, str]:
    """Build NewAPI soft-affinity headers for the current logical session."""
    if not _is_tode_newapi(base_url):
        return {}

    conversation_root = get_conversation_context() or session_id
    conversation_id = _opaque_affinity_id("conversation", conversation_root)
    if not conversation_id:
        return {}

    headers = {_CONVERSATION_HEADER: conversation_id}
    if is_delegated_child_context():
        lane_id = _opaque_affinity_id("lane", session_id)
        if lane_id:
            headers[_LANE_HEADER] = lane_id
    return headers


class TodeNewAPIProfile(ProviderProfile):
    """Attach session affinity to every TODE NewAPI model request."""

    def build_api_kwargs_extras(
        self,
        *,
        session_id: str | None = None,
        **context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        headers = build_affinity_headers(
            base_url=context.get("base_url"),
            session_id=session_id,
        )
        return {}, {"extra_headers": headers} if headers else {}


tode = TodeNewAPIProfile(
    name="tode",
    display_name="TODE NewAPI",
    description="TODE NewAPI with Hermes session-aware soft affinity",
    env_vars=("OPENAI_API_KEY",),
    base_url="https://newapi.tode.ltd/v1",
)

register_provider(tode)
