# Fork patch notes

This branch is based on `NousResearch/hermes-agent` `main` at `v0.16.0`.
It carries Slack `send_message` fixes for forwarding attachments and opening
DMs without a manually pre-opened Slack conversation.

## Testing

Run the focused tests for this patch set:

```sh
.venv/bin/python -m pytest tests/tools/test_send_message_tool.py tests/hermes_cli/test_slack_cli.py
```

## Patches

### 1. Slack media uploads for `send_message`

- **Source:** [`briandevans/hermes-agent` `fix/slack-send-message-media-17261`](https://github.com/briandevans/hermes-agent/commits/fix/slack-send-message-media-17261), opened upstream as [PR #17348](https://github.com/NousResearch/hermes-agent/pull/17348).
- **What it does:** Adds a `Platform.SLACK + media_files` branch in `_send_to_platform` and extends `_send_slack` to call `files_upload_v2` with `media_files=` and `thread_id=`.
- **Why we carry it:** Upstream `v0.16.0` still drops Slack `MEDIA:` attachments from `send_message` with the “MEDIA attachments were omitted for slack” warning.
- **Drop when:** Upstream supports native Slack media attachments in `send_message`.

### 2. Slack U/W user IDs open DMs before sending

- **What it does:** Accepts Slack `U...` and `W...` user IDs as explicit `send_message` targets and converts them to `D...` DM channel IDs with `conversations.open` before `chat.postMessage` or `files_upload_v2`.
- **Why we carry it:** Agents need to send to resolved Slack users without asking the operator for a pre-existing DM channel ID.
- **Drop when:** Upstream `_send_slack` performs the same `conversations.open` conversion.

### 3. Slack user lookup by email/name/handle

- **What it does:** Extends Slack targets to accept `slack:email:user@example.com`, `slack:mailto:user@example.com`, `slack:@handle`, `slack:user:Real Name`, and bare name/handle fallback after channel-directory misses. The tool resolves those targets with `users.lookupByEmail` or `users.list`, then reuses the U/W-to-D send path.
- **Manifest:** Adds `users:read.email` to `hermes slack manifest`; `users:read` already covers name and handle search.
- **Why we carry it:** Agents should be able to forward an attachment to a Slack user from a human-facing target without manual DM setup.
- **Drop when:** Upstream `send_message` can resolve Slack users and send text/media to the resulting DM.
