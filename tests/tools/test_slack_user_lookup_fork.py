"""Fork patch tests: Slack email targets and Enterprise Grid (W...) user IDs for send_message."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.send_message_senders import _resolve_slack_user_target
from tools.send_message_targets import _parse_target_ref


def _response(data):
    response = MagicMock()
    response.json = AsyncMock(return_value=data)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    return response


def _session(*responses):
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.post = MagicMock(side_effect=responses)
    return session


def _resolve(chat_id, *responses):
    session = _session(*responses)
    with patch("aiohttp.ClientSession", return_value=session):
        result = asyncio.run(_resolve_slack_user_target("tok", chat_id))
    return result, session


class TestParse:
    @pytest.mark.parametrize("ref", [
        "email:recipient@example.test",
        "mailto:recipient@example.test",
        "EMAIL:Recipient@Example.test",
        "recipient@example.test",
    ])
    def test_email_forms_parse_to_user_email(self, ref):
        chat_id, thread_id, explicit = _parse_target_ref("slack", ref)
        assert chat_id.startswith("user_email:")
        assert chat_id.split(":", 1)[1].lower() == "recipient@example.test"
        assert thread_id is None
        assert explicit is True

    @pytest.mark.parametrize("ref", ["W123ABCDEF", "U123ABCDEF"])
    def test_user_and_grid_ids_parse_to_user_target(self, ref):
        assert _parse_target_ref("slack", ref) == (f"user:{ref}", None, True)

    @pytest.mark.parametrize("ref", ["#engineering", "email:", "email:not-an-address", "@"])
    def test_non_email_refs_are_not_email_targets(self, ref):
        chat_id, _thread, _explicit = _parse_target_ref("slack", ref)
        assert not (chat_id or "").startswith("user_email:")

    def test_conversation_ids_unchanged(self):
        assert _parse_target_ref("slack", "C123ABCDEF") == ("C123ABCDEF", None, True)
        assert _parse_target_ref("slack", "D123ABCDEF") == ("D123ABCDEF", None, True)


class TestEmailResolution:
    def test_email_resolves_to_dm_channel(self):
        (chat_id, err), session = _resolve(
            "user_email:recipient@example.test",
            _response({"ok": True, "user": {"id": "U0RECIPIENT"}}),
            _response({"ok": True, "channel": {"id": "D0RECIPIENT"}}),
        )
        assert (chat_id, err) == ("D0RECIPIENT", None)
        methods = [call.args[0].rsplit("/", 1)[1] for call in session.post.call_args_list]
        assert methods == ["users.lookupByEmail", "conversations.open"]
        lookup_kwargs = session.post.call_args_list[0].kwargs
        # users.lookupByEmail only accepts form-encoded params; a JSON body returns invalid_arguments.
        assert lookup_kwargs["data"] == {"email": "recipient@example.test"}
        assert "json" not in lookup_kwargs
        assert "Content-Type" not in lookup_kwargs["headers"]
        assert session.post.call_args_list[1].kwargs["json"] == {"users": "U0RECIPIENT"}

    def test_grid_user_id_opens_dm(self):
        (chat_id, err), _ = _resolve(
            "user:W0GRIDUSER",
            _response({"ok": True, "channel": {"id": "D0GRIDUSER"}}),
        )
        assert (chat_id, err) == ("D0GRIDUSER", None)

    def test_missing_scope_names_the_scope(self):
        (chat_id, err), session = _resolve(
            "user_email:recipient@example.test",
            _response({"ok": False, "error": "missing_scope"}),
        )
        assert chat_id is None
        assert "users:read.email" in err["error"]
        assert session.post.call_count == 1  # never reaches conversations.open

    def test_unknown_email_is_an_error_and_sends_nothing(self):
        (chat_id, err), session = _resolve(
            "user_email:nobody@example.test",
            _response({"ok": False, "error": "users_not_found"}),
        )
        assert chat_id is None
        assert "No Slack user found" in err["error"]
        assert session.post.call_count == 1

    @pytest.mark.parametrize("user", [{"id": "U0GONE", "deleted": True}, {"id": "B0BOT", "is_bot": True}, {}])
    def test_deactivated_bot_or_empty_user_is_refused(self, user):
        (chat_id, err), session = _resolve(
            "user_email:recipient@example.test",
            _response({"ok": True, "user": user}),
        )
        assert chat_id is None
        assert err and "error" in err
        assert session.post.call_count == 1  # fail closed: no DM opened

    def test_other_lookup_errors_surface(self):
        (chat_id, err), _ = _resolve(
            "user_email:recipient@example.test",
            _response({"ok": False, "error": "ratelimited"}),
        )
        assert chat_id is None
        assert "ratelimited" in err["error"]

    def test_non_user_targets_pass_through_without_api_calls(self):
        for cid in ("C0B0QV5434G", "D123ABCDEF"):
            (chat_id, err), session = _resolve(cid)
            assert (chat_id, err) == (cid, None)
            assert session.post.call_count == 0
