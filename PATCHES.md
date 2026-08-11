# Fork patch notes

This branch is based on `NousResearch/hermes-agent` at tag **`v2026.7.7.2`**
(release `v0.18.2`). It carries Slack `send_message` fixes for resolving users
by email/name/handle, opening DMs to user IDs, and forwarding media
attachments.

Upstream #41112 migrated Slack from `gateway/platforms/slack.py` to the bundled
plugin `plugins/platforms/slack/`, and standalone/cron delivery now flows
through the platform registry's `standalone_sender_fn`
(`plugins/platforms/slack/adapter.py::_standalone_send`). The patches below were
re-homed to that architecture from the previous `v0.16.0` base — see the
"Where it lives now" note on each.

## Testing

The patch set has a dedicated, isolated test module so the surface stays
obvious across future rebases:

```sh
uv run pytest tests/tools/test_slack_user_media_fork.py
```

Regression-check the shared paths we touch (target parsing + send routing):

```sh
uv run pytest tests/tools/test_send_message_tool.py
```

> Note: `tests/gateway/test_slack.py` + `tests/gateway/test_slack_channel_session_scope.py`
> have a **pre-existing** cross-file ordering failure (11 tests) that
> reproduces on the clean `v2026.7.7.2` tag — unrelated to these patches.

## Patches

### 1. Slack media uploads for `send_message`

- **What it does:** Adds a `Platform.SLACK and media_files` branch in
  `tools/send_message_tool.py::_send_to_platform` (mirroring the Feishu/WhatsApp
  registry pattern) and extends the Slack plugin's `_standalone_send` to upload
  each attachment with `files_upload_v2` (text becomes the first file's
  `initial_comment`; `thread_id` → `thread_ts`).
- **Where it lives now:** `tools/send_message_tool.py` (routing) +
  `plugins/platforms/slack/adapter.py::_standalone_send` (upload). Previously
  lived in `_send_slack` inside `send_message_tool.py`.
- **Why we carry it:** Upstream still drops Slack `MEDIA:` attachments from
  `send_message` with the "MEDIA attachments were omitted for slack" warning —
  its `_standalone_send` is text-only.
- **Drop when:** Upstream's Slack `_standalone_send` supports `media_files`.

### 2. Slack U/W user IDs open DMs before sending

- **What it does:** Accepts Slack `U...` and `W...` user IDs as explicit
  `send_message` targets and converts them to `D...` DM channel IDs with
  `conversations.open` before delivery.
- **Where it lives now:** `_SLACK_TARGET_RE` widened to `[CGDUW]` and
  `_parse_target_ref` marks U/W as explicit; the `conversations.open`
  conversion is upstream's own inline block in `_handle_send`, which we widened
  from `U`-only to also handle `W` (Enterprise Grid workspace user IDs).
- **Why we carry it:** Agents need to send to resolved Slack users without a
  pre-opened DM channel ID; upstream only converts `U`, not `W`.
- **Drop when:** Upstream's `_handle_send` handles `W` IDs too.

### 3. Slack user lookup by email/name/handle

- **What it does:** Extends Slack targets to accept
  `slack:email:user@example.com`, `slack:mailto:user@example.com`,
  `slack:@handle`, `slack:user:Real Name`, and a bare name/handle fallback after
  channel-directory misses. Resolves them with `users.lookupByEmail` or
  `users.list` to a `U`-id, which then flows into the Patch 2 U/W→DM path.
- **Where it lives now:** Entirely in `tools/send_message_tool.py`
  (`_parse_slack_user_lookup_ref`, `_encode/_decode/_is_slack_user_lookup`,
  `_resolve_slack_lookup_target`, and the resolution step in `_handle_send`).
  The proxy-helper import was repointed from `gateway.platforms.slack` to
  `plugins.platforms.slack.adapter` (moved in #41112).
- **Manifest:** Adds `users:read.email` to `hermes slack manifest`
  (`hermes_cli/slack_cli.py`); `users:read` already covers name/handle search.
- **Why we carry it:** Agents should forward to a human-facing Slack target
  without manual DM setup.
- **Drop when:** Upstream `send_message` can resolve Slack users and send
  text/media to the resulting DM.

### 4. Register `send_message` as an agent-callable tool

- **What it does:** Registers the existing `send_message` tool in the registry
  (`tools/send_message_tool.py`) and adds a `messaging` toolset (`toolsets.py`,
  plus `send_message` in `_HERMES_CORE_TOOLS`), so the model can post to a
  connected messaging platform (e.g. forward a result to a Slack channel) from
  within a session. Upstream ships the schema, handler, and `_check_send_message`
  gate but intentionally leaves it unregistered.
- **Where it lives now:** `tools/send_message_tool.py` (the `# --- Registry ---`
  block) and `toolsets.py` (`messaging` toolset + core-tools entry).
- **Gating:** `_check_send_message` restricts it to live-gateway / kanban-worker
  sessions, so it never appears in bare CLI/cron schemas.
- **Why we carry it:** Agents running as a gateway bot should be able to forward
  their own results to a channel without a human relaying them.
- **Drop when:** Upstream registers `send_message` as an agent tool.
