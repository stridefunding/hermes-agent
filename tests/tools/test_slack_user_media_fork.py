"""Fork-patch tests for Slack user resolution + media on send_message.

These cover the fork patches documented in PATCHES.md, ported to the
v2026.7.7.2 plugin architecture (Slack moved to plugins/platforms/slack/ in
upstream #41112):

  * Patch 1 — native media uploads via files_upload_v2 (plugin _standalone_send
    + the Slack media branch in tools.send_message_tool._send_to_platform).
  * Patch 2 — U/W user IDs open a DM via conversations.open before sending
    (handled in _handle_send; _parse_target_ref marks U/W as explicit).
  * Patch 3 — Slack targets accept email / @handle / name and resolve to a U-id
    via users.lookupByEmail / users.list.

Kept in a dedicated module so the patch surface stays obvious across future
upstream rebases (mirrors how PATCHES.md isolates the fork).
"""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform
from tools.send_message_tool import (
    _decode_slack_user_lookup,
    _encode_slack_user_lookup,
    _is_slack_user_lookup,
    _parse_slack_user_lookup_ref,
    _parse_target_ref,
    _resolve_slack_lookup_target,
    _send_to_platform,
)


class FakeSlackApiError(Exception):
    """Module-level so the class identity is stable across installs — the code
    under test does ``from slack_sdk.errors import SlackApiError`` at call time,
    so its ``except`` must reference the same object our mocks raise."""

    def __init__(self, message="", response=None):
        super().__init__(message)
        self.response = response


def _install_slack_sdk_mock(monkeypatch, *, lookup_mock=None, list_mock=None, upload_mock=None):
    """Install a fake slack_sdk into sys.modules and return the client class + error."""
    slack_sdk = MagicMock()

    class FakeAsyncWebClient:
        def __init__(self, token=None):
            self.token = token
            self.users_lookupByEmail = lookup_mock or AsyncMock()
            self.users_list = list_mock or AsyncMock()
            self.files_upload_v2 = upload_mock or AsyncMock()

    slack_sdk.web.async_client.AsyncWebClient = FakeAsyncWebClient
    slack_sdk.errors.SlackApiError = FakeSlackApiError
    monkeypatch.setitem(sys.modules, "slack_sdk", slack_sdk)
    monkeypatch.setitem(sys.modules, "slack_sdk.web", slack_sdk.web)
    monkeypatch.setitem(sys.modules, "slack_sdk.web.async_client", slack_sdk.web.async_client)
    monkeypatch.setitem(sys.modules, "slack_sdk.errors", slack_sdk.errors)
    return FakeAsyncWebClient, FakeSlackApiError


# ---------------------------------------------------------------------------
# Patch 2/3 — target parsing (pure, no network)
# ---------------------------------------------------------------------------


class TestSlackTargetParsing:
    def test_u_id_is_explicit(self):
        assert _parse_target_ref("slack", "U012345678") == ("U012345678", None, True)

    def test_w_id_is_explicit(self):
        assert _parse_target_ref("slack", "W012345678") == ("W012345678", None, True)

    def test_channel_id_is_explicit(self):
        assert _parse_target_ref("slack", "C012345678") == ("C012345678", None, True)

    def test_dm_id_is_explicit(self):
        assert _parse_target_ref("slack", "D012345678") == ("D012345678", None, True)

    def test_thread_target_preserves_thread_ts(self):
        assert _parse_target_ref("slack", "C012345678:1700.0001") == (
            "C012345678",
            "1700.0001",
            True,
        )

    def test_email_target_becomes_lookup_token(self):
        chat_id, thread_id, explicit = _parse_target_ref("slack", "email:me@example.test")
        assert explicit is True
        assert thread_id is None
        assert _is_slack_user_lookup(chat_id)
        assert _decode_slack_user_lookup(chat_id) == ("email", "me@example.test")

    def test_bare_email_becomes_lookup_token(self):
        chat_id, _thread, explicit = _parse_target_ref("slack", "me@example.test")
        assert explicit is True
        assert _decode_slack_user_lookup(chat_id) == ("email", "me@example.test")

    def test_handle_target_becomes_query_lookup(self):
        chat_id, _thread, explicit = _parse_target_ref("slack", "@example.recipient")
        assert explicit is True
        assert _decode_slack_user_lookup(chat_id) == ("query", "example.recipient")

    def test_user_prefix_becomes_query_lookup(self):
        chat_id, _thread, explicit = _parse_target_ref("slack", "user:Example Recipient")
        assert explicit is True
        assert _decode_slack_user_lookup(chat_id) == ("query", "Example Recipient")

    def test_bare_name_is_not_explicit_in_parse(self):
        # allow_bare=False in _parse_target_ref: a bare name is NOT resolved here
        # so it can still hit the channel directory first in _handle_send.
        assert _parse_target_ref("slack", "Example Recipient") == (None, None, False)

    def test_channel_hash_is_not_a_user_lookup(self):
        assert _parse_slack_user_lookup_ref("#engineering", allow_bare=True) is None

    def test_bare_name_is_a_query_lookup_when_allowed(self):
        assert _parse_slack_user_lookup_ref("Example Recipient", allow_bare=True) == (
            "query",
            "Example Recipient",
        )

    def test_encode_decode_roundtrip(self):
        token = _encode_slack_user_lookup("email", "a@b.co")
        assert _is_slack_user_lookup(token)
        assert _decode_slack_user_lookup(token) == ("email", "a@b.co")

    def test_no_slack_lookup_token_for_other_platforms(self):
        # The email/@handle → lookup-token behavior is Slack-only; other
        # platforms must never emit a synthetic slack lookup target.
        for platform, ref in [
            ("discord", "999888777"),
            ("telegram", "@somebody"),
            ("email", "me@example.test"),
        ]:
            chat_id, _t, _e = _parse_target_ref(platform, ref)
            assert not _is_slack_user_lookup(chat_id or "")


# ---------------------------------------------------------------------------
# Patch 3 — lookup resolver (mocked slack_sdk)
# ---------------------------------------------------------------------------


class TestSlackLookupResolver:
    def test_email_lookup_uses_users_lookup_by_email(self, monkeypatch):
        lookup = AsyncMock(return_value={"user": {"id": "U123TEST01"}})
        _install_slack_sdk_mock(monkeypatch, lookup_mock=lookup)

        user_id, err = asyncio.run(
            _resolve_slack_lookup_target(
                "xoxb-test", _encode_slack_user_lookup("email", "recipient@example.test")
            )
        )

        assert err is None
        assert user_id == "U123TEST01"
        lookup.assert_awaited_once_with(email="recipient@example.test")

    def test_email_missing_scope_surfaces_actionable_error(self, monkeypatch):
        resp = SimpleNamespace(get=lambda k: "missing_scope" if k == "error" else None)
        lookup = AsyncMock(side_effect=FakeSlackApiError("nope", response=resp))
        _install_slack_sdk_mock(monkeypatch, lookup_mock=lookup)

        user_id, err = asyncio.run(
            _resolve_slack_lookup_target(
                "xoxb-test", _encode_slack_user_lookup("email", "recipient@example.test")
            )
        )
        assert user_id == ""
        assert err is not None
        assert "users:read.email" in err["error"]

    def test_query_lookup_prefers_exact_active_human_match(self, monkeypatch):
        users_list = AsyncMock(
            return_value={
                "members": [
                    {"id": "UDELETED01", "deleted": True, "profile": {"real_name": "Example Recipient"}},
                    {"id": "UBOT000001", "is_bot": True, "profile": {"real_name": "Example Recipient"}},
                    {
                        "id": "U123TEST01",
                        "profile": {"real_name": "Example Recipient", "display_name": "example.recipient"},
                    },
                ],
                "response_metadata": {},
            }
        )
        _install_slack_sdk_mock(monkeypatch, list_mock=users_list)

        user_id, err = asyncio.run(
            _resolve_slack_lookup_target(
                "xoxb-test", _encode_slack_user_lookup("query", "Example Recipient")
            )
        )

        assert err is None
        assert user_id == "U123TEST01"
        users_list.assert_awaited_once_with(limit=200)

    def test_query_lookup_reports_ambiguity(self, monkeypatch):
        users_list = AsyncMock(
            return_value={
                "members": [
                    {"id": "U111111111", "profile": {"real_name": "Example One"}},
                    {"id": "U222222222", "profile": {"real_name": "Example Two"}},
                ],
                "response_metadata": {},
            }
        )
        _install_slack_sdk_mock(monkeypatch, list_mock=users_list)

        user_id, err = asyncio.run(
            _resolve_slack_lookup_target(
                "xoxb-test", _encode_slack_user_lookup("query", "Example")
            )
        )
        assert user_id == ""
        assert err is not None
        assert "Ambiguous Slack user target" in err["error"]
        assert "U111111111" in err["error"] and "U222222222" in err["error"]

    def test_query_lookup_no_match(self, monkeypatch):
        users_list = AsyncMock(return_value={"members": [], "response_metadata": {}})
        _install_slack_sdk_mock(monkeypatch, list_mock=users_list)
        user_id, err = asyncio.run(
            _resolve_slack_lookup_target("xoxb-test", _encode_slack_user_lookup("query", "Ghost"))
        )
        assert user_id == ""
        assert "No Slack user matched" in err["error"]


# ---------------------------------------------------------------------------
# Patch 1 — plugin _standalone_send media uploads (mocked slack_sdk)
# ---------------------------------------------------------------------------


class TestStandaloneSendMedia:
    def _send(self, pconfig, chat_id, message, **kw):
        from plugins.platforms.slack.adapter import _standalone_send

        return asyncio.run(_standalone_send(pconfig, chat_id, message, **kw))

    def test_media_uploads_via_files_upload_v2(self, monkeypatch, tmp_path):
        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        upload = AsyncMock(return_value={"files": [{"id": "F123"}]})
        _install_slack_sdk_mock(monkeypatch, upload_mock=upload)

        result = self._send(
            SimpleNamespace(token="xoxb-tok"),
            "D123CHANNEL",
            "here is the report",
            media_files=[(str(pdf), False)],
        )

        assert result["success"] is True
        assert result["message_id"] == "F123"
        upload.assert_awaited_once()
        kwargs = upload.await_args.kwargs
        assert kwargs["channel"] == "D123CHANNEL"
        assert kwargs["file"] == str(pdf)
        assert kwargs["filename"] == "report.pdf"
        assert kwargs["initial_comment"] == "here is the report"

    def test_text_attaches_only_to_first_file(self, monkeypatch, tmp_path):
        a = tmp_path / "a.png"
        b = tmp_path / "b.png"
        a.write_bytes(b"\x89PNG")
        b.write_bytes(b"\x89PNG")
        upload = AsyncMock(return_value={"files": [{"id": "F1"}]})
        _install_slack_sdk_mock(monkeypatch, upload_mock=upload)

        result = self._send(
            SimpleNamespace(token="xoxb-tok"),
            "D123CHANNEL",
            "caption",
            media_files=[(str(a), False), (str(b), False)],
        )
        assert result["success"] is True
        assert upload.await_count == 2
        first_comment = upload.await_args_list[0].kwargs["initial_comment"]
        second_comment = upload.await_args_list[1].kwargs["initial_comment"]
        assert first_comment == "caption"
        assert second_comment == ""

    def test_thread_id_forwarded_as_thread_ts(self, monkeypatch, tmp_path):
        img = tmp_path / "a.png"
        img.write_bytes(b"\x89PNG")
        upload = AsyncMock(return_value={"files": [{"id": "F1"}]})
        _install_slack_sdk_mock(monkeypatch, upload_mock=upload)

        self._send(
            SimpleNamespace(token="xoxb-tok"),
            "C123",
            "hi",
            media_files=[(str(img), False)],
            thread_id="1700.5",
        )
        assert upload.await_args.kwargs["thread_ts"] == "1700.5"

    def test_missing_media_file_returns_error(self, monkeypatch, tmp_path):
        _install_slack_sdk_mock(monkeypatch)
        result = self._send(
            SimpleNamespace(token="xoxb-tok"),
            "C123",
            "hi",
            media_files=[(str(tmp_path / "nope.png"), False)],
        )
        assert "error" in result
        assert "not found" in result["error"]

    def test_upload_api_error_is_surfaced(self, monkeypatch, tmp_path):
        img = tmp_path / "a.png"
        img.write_bytes(b"\x89PNG")
        resp = SimpleNamespace(get=lambda k: "not_in_channel" if k == "error" else None)
        upload = AsyncMock(side_effect=FakeSlackApiError("boom", response=resp))
        _install_slack_sdk_mock(monkeypatch, upload_mock=upload)
        result = self._send(
            SimpleNamespace(token="xoxb-tok"),
            "C123",
            "hi",
            media_files=[(str(img), False)],
        )
        assert "error" in result
        assert "not_in_channel" in result["error"]


# ---------------------------------------------------------------------------
# Patch 1 — _send_to_platform routes Slack media to the plugin sender
# ---------------------------------------------------------------------------


class TestSendToPlatformSlackMediaRouting:
    def _fake_registry(self, monkeypatch, sender):
        entry = SimpleNamespace(standalone_sender_fn=sender, max_message_length=40000)
        from gateway import platform_registry as _mod

        monkeypatch.setattr(_mod.platform_registry, "get", lambda name: entry)
        return entry

    def test_slack_media_routes_to_standalone_sender(self, monkeypatch, tmp_path):
        img = tmp_path / "a.png"
        img.write_bytes(b"\x89PNG")
        sender = AsyncMock(return_value={"success": True, "message_id": "F1"})
        self._fake_registry(monkeypatch, sender)

        result = asyncio.run(
            _send_to_platform(
                Platform.SLACK,
                SimpleNamespace(token="xoxb-tok", extra={}),
                "D123",
                "caption",
                thread_id=None,
                media_files=[(str(img), False)],
            )
        )
        assert result["success"] is True
        sender.assert_awaited_once()
        assert sender.await_args.kwargs["media_files"] == [(str(img), False)]
        assert sender.await_args.kwargs["thread_id"] is None

    def test_slack_text_only_does_not_pass_media(self, monkeypatch):
        sender = AsyncMock(return_value={"success": True, "message_id": "1700.1"})
        self._fake_registry(monkeypatch, sender)

        result = asyncio.run(
            _send_to_platform(
                Platform.SLACK,
                SimpleNamespace(token="xoxb-tok", extra={}),
                "C123",
                "just text",
                thread_id=None,
                media_files=[],
            )
        )
        assert result["success"] is True
        # text path calls the sender positionally without media_files kwarg
        assert "media_files" not in sender.await_args.kwargs
