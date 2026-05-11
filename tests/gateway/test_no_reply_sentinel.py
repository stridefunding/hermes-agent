"""Tests for the [dexter-local] NO_REPLY sentinel in gateway/platforms/base.py.

When the model emits the exact token NO_REPLY (typically driven by a prompt
rule like "in group threads, respond with NO_REPLY when you have nothing to
add"), _process_message_background treats it as an explicit silent reply and
skips delivery. Mirrors stevengonsalvez's proposal on
NousResearch/hermes-agent#13248 (2026-05-07).
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.session import SessionSource


class _CapturingAdapter(BasePlatformAdapter):
    """Minimal adapter that records every send() call."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.sent: list[tuple[str, str]] = []

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def send(self, chat_id, content="", **kwargs):
        self.sent.append((chat_id, content))
        return SendResult(success=True, message_id="m-1")

    async def get_chat_info(self, chat_id):
        return {}


def _adapter():
    return _CapturingAdapter(
        PlatformConfig(enabled=True, token="t"), Platform.TELEGRAM
    )


def _event(text="anything"):
    return MessageEvent(
        text=text,
        message_id="msg-1",
        source=SessionSource(
            platform=Platform.TELEGRAM, chat_id="42", user_id="u-1"
        ),
        message_type=MessageType.TEXT,
    )


@pytest.mark.asyncio
async def test_no_reply_sentinel_suppresses_delivery():
    """Handler returning exactly 'NO_REPLY' → no send() call."""
    adapter = _adapter()
    adapter._send_with_retry = AsyncMock(
        return_value=SendResult(success=True, message_id="x")
    )

    async def _handler(evt):
        return "NO_REPLY"

    adapter.set_message_handler(_handler)

    with patch("gateway.platforms.base.asyncio.sleep", AsyncMock()), patch.object(
        adapter, "_keep_typing", new=AsyncMock()
    ):
        await adapter._process_message_background(_event(), "agent:main:telegram:private:42")
        for _ in range(5):
            await asyncio.sleep(0)

    adapter._send_with_retry.assert_not_called()


@pytest.mark.asyncio
async def test_no_reply_with_trailing_whitespace_still_suppresses():
    """`.strip()` handles trailing newlines/spaces the model may emit."""
    adapter = _adapter()
    adapter._send_with_retry = AsyncMock(
        return_value=SendResult(success=True, message_id="x")
    )

    async def _handler(evt):
        return "NO_REPLY\n"

    adapter.set_message_handler(_handler)

    with patch("gateway.platforms.base.asyncio.sleep", AsyncMock()), patch.object(
        adapter, "_keep_typing", new=AsyncMock()
    ):
        await adapter._process_message_background(_event(), "agent:main:telegram:private:42")
        for _ in range(5):
            await asyncio.sleep(0)

    adapter._send_with_retry.assert_not_called()


@pytest.mark.asyncio
async def test_normal_response_delivers_as_usual():
    """Regression guard: any non-sentinel response is delivered normally."""
    adapter = _adapter()
    adapter._send_with_retry = AsyncMock(
        return_value=SendResult(success=True, message_id="x")
    )

    async def _handler(evt):
        return "hello there"

    adapter.set_message_handler(_handler)

    with patch("gateway.platforms.base.asyncio.sleep", AsyncMock()), patch.object(
        adapter, "_keep_typing", new=AsyncMock()
    ):
        await adapter._process_message_background(_event(), "agent:main:telegram:private:42")
        for _ in range(5):
            await asyncio.sleep(0)

    adapter._send_with_retry.assert_called_once()


@pytest.mark.asyncio
async def test_no_reply_substring_does_not_suppress():
    """Only an exact-match (post-strip) NO_REPLY suppresses — substring is a normal message."""
    adapter = _adapter()
    adapter._send_with_retry = AsyncMock(
        return_value=SendResult(success=True, message_id="x")
    )

    async def _handler(evt):
        return "I'll send NO_REPLY when there's nothing to add"

    adapter.set_message_handler(_handler)

    with patch("gateway.platforms.base.asyncio.sleep", AsyncMock()), patch.object(
        adapter, "_keep_typing", new=AsyncMock()
    ):
        await adapter._process_message_background(_event(), "agent:main:telegram:private:42")
        for _ in range(5):
            await asyncio.sleep(0)

    adapter._send_with_retry.assert_called_once()
