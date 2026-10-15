# Fork patch notes

This branch is based on `NousResearch/hermes-agent` at tag **`v0.22.2`**. It carries a small set
of generic Slack `send_message` improvements.

Patches carried on earlier bases that upstream now ships natively, and were therefore dropped:

- Slack media attachments from `send_message` (`files_upload_v2` in `_standalone_send`)
- `conversations.open` for `U...` user targets
- `aiohttp` 3.14.3 pin
- Slack thread-context / parent-text extraction from Block Kit blocks and attachments

## Testing

```sh
python -m pytest tests/tools/test_slack_user_lookup_fork.py tests/tools/test_send_message_registration_fork.py
```

Regression-check the shared paths:

```sh
python -m pytest tests/tools/test_send_message_slack.py tests/tools/test_send_message_target_parse.py \
  tests/tools/test_send_message_relay_target_authz.py
```

## Patches

### 1. Slack user lookup by email

- **What it does:** Accepts `slack:email:user@example.com`, `slack:mailto:user@example.com` and a
  bare address as targets. The address is resolved with `users.lookupByEmail`, then the user's DM is
  opened with `conversations.open`. Deactivated users, bots and unknown addresses fail closed and
  nothing is sent. A missing `users:read.email` scope produces an actionable error.
- **Where it lives:** `tools/send_message_targets.py` (parser, `user_email:` target),
  `tools/send_message_senders.py::_resolve_slack_user_target`, and the `users:read.email` scope in
  `hermes_cli/slack_cli.py` (`hermes slack manifest`).
- **Scope note:** Email is a unique identifier. Display and real names are intentionally not
  searched, because they are mutable and non-unique, and a wrong match would deliver to the wrong
  person. Upstream's `@handle` lookup is unchanged.
- **Drop when:** Upstream resolves Slack users by email in `send_message`.

### 2. Slack `W...` (Enterprise Grid) user IDs as targets

- **What it does:** Upstream converts `U...` user IDs to DMs but does not recognise `W...`
  Enterprise Grid member IDs in the tool-level target parser. This widens the user-ID pattern to
  `[UW]`.
- **Where it lives:** `tools/send_message_targets.py::_SLACK_USER_ID_RE`,
  `tools/send_message_tool.py::_slack_dm_chat_id`.
- **Drop when:** Upstream's tool-level parser accepts `W...` IDs.

### 3. `send_message` as an agent-callable tool

- **What it does:** Registers `send_message` in the tool registry (`messaging` toolset, also listed
  in `_HERMES_CORE_TOOLS`) so a gateway-hosted agent can DM people and post to channels itself.
  Upstream keeps the helpers but deliberately leaves the tool unregistered.
- **Gating:** `_check_send_message` opens the tool only for a live non-local platform session
  (`HERMES_SESSION_PLATFORM`) or a kanban worker (`HERMES_KANBAN_TASK`). Bare CLI and cron sessions
  never see it, and any other state fails closed.
- **Where it lives:** `tools/send_message_tool.py` (`SEND_MESSAGE_SCHEMA`, `_check_send_message`,
  registry block at the bottom) and `toolsets.py`.
- **Tests:** `tests/tools/test_send_message_registration_fork.py`.
- **Drop when:** Upstream registers `send_message` as an agent tool.

### Known unrelated failures

`tests/tools/test_tool_search.py::TestDeferredCallSchemaProbe` (4 tests) fails on the clean
`v0.22.2` tag as well.
