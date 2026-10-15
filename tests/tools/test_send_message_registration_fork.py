"""Fork patch tests: send_message is an agent-callable tool, gated to live platform sessions."""

import pytest

import tools.send_message_tool as smt
from tools.registry import registry


def _set_platform(monkeypatch, value):
    monkeypatch.setattr(
        "gateway.session_context.get_session_env",
        lambda name, default="": value if name == "HERMES_SESSION_PLATFORM" else default,
    )


def test_registered_in_messaging_toolset():
    entry = registry.get_entry("send_message") if hasattr(registry, "get_entry") else None
    if entry is None:
        entry = registry._tools["send_message"]
    assert entry.toolset == "messaging"
    assert entry.check_fn is smt._check_send_message
    assert entry.schema["name"] == "send_message"


def test_toolsets_expose_send_message():
    import toolsets

    assert "send_message" in toolsets._HERMES_CORE_TOOLS
    assert toolsets.TOOLSETS["messaging"]["tools"] == ["send_message"]


@pytest.mark.parametrize("platform", ["slack", "telegram", "discord"])
def test_gate_open_in_live_platform_session(monkeypatch, platform):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    _set_platform(monkeypatch, platform)
    assert smt._check_send_message() is True


@pytest.mark.parametrize("platform", ["", "local", None])
def test_gate_closed_for_cli_and_cron(monkeypatch, platform):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    _set_platform(monkeypatch, platform or "")
    assert smt._check_send_message() is False


def test_gate_open_for_kanban_worker(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    _set_platform(monkeypatch, "")
    assert smt._check_send_message() is True


def test_schema_advertises_slack_user_targets():
    desc = smt.SEND_MESSAGE_SCHEMA["parameters"]["properties"]["target"]["description"]
    assert "slack:email:" in desc and "slack:@handle" in desc
