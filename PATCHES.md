# Dexter patches on this fork

This branch (`dexter`) carries a small set of patches on top of
`NousResearch/hermes-agent`'s `main`. Patches are tracked here; the order
in this file matches the commit order on `dexter` (oldest first).

**Subject prefix tells you the lifecycle:**

- `[dexter-pin]` — cherry-pick of someone else's open upstream PR. Drops
  on the next `make fork-rebase` after that PR merges upstream.
- `[dexter-local]` — a patch we wrote (or carry from a comment thread).
  Drops when upstream independently lands an equivalent fix.

**Important: we don't push these upstream.** See
`feedback_no_upstream_hermes_prs.md` in the operator's memory. NousResearch's
AI-augmented contribution policy is unclear; the fork is the only place
these patches live.

## Testing

Run only the test files covering modules we touched:

```sh
.venv/bin/python -m pytest \
  tests/tools/test_send_message_tool.py \
  tests/gateway/test_no_reply_sentinel.py \
  tests/gateway/test_ephemeral_reply.py
```

As of 2026-05-11: **147 passed**.

We do not gate on the full Hermes suite — Dexter uses a subset of
Hermes (Slack + Anthropic + a few MCPs); the rest is not our problem
unless it intersects with what we patch.

## Patches

### 1. `[dexter-pin]` fix(send_message): native Slack media uploads on cross-channel/DM sends

- **Commit:** `d614650b` on `dexter`
- **Source:** [`briandevans/hermes-agent@8af96da`](https://github.com/briandevans/hermes-agent/commits/fix/slack-send-message-media-17261), open as [PR #17348](https://github.com/NousResearch/hermes-agent/pull/17348) against upstream
- **What it does:** Adds a `Platform.SLACK + media_files` branch in `_send_to_platform` and extends `_send_slack` to accept `media_files=` / `thread_id=`. Without it, cross-channel and DM sends through `send_message` drop attachments with "MEDIA attachments were omitted for slack".
- **Why we carry it:** Phase 1 Dexter needs cross-channel/DM media for receipts. Upstream PR has been stalled since 2026-04-29; `test` job is currently red on a flaky check, not on this patch's logic.
- **Cherry-pick notes:** Did not apply via `git cherry-pick` due to a ~700-line conflict in `tests/tools/test_send_message_tool.py` caused by unrelated upstream growth. Conflict was structurally clean (PR's tests are pure additions) — applied by manually appending the two new test classes at end-of-file. One existing upstream test (`test_slack_messages_are_formatted_before_send`) updated to assert the new `thread_id=None` kwarg.
- **Drop when:** PR #17348 merges into upstream main.

### 2. `[dexter-local]` fix(slack): open DM via conversations.open for U/W targets (completes 75d3eaa0)

- **Commit:** `39311191` on `dexter`
- **Context:** Upstream commit [`75d3eaa0`](https://github.com/NousResearch/hermes-agent/commit/75d3eaa0) narrowed `_SLACK_TARGET_RE` from `[CGDUW]` to `[CGD]` to stop silent retry loops on user-id sends, and its commit message explicitly noted the fix was incomplete: *"To DM a user you must first call conversations.open to obtain a D... ID."*
- **What it does:** Re-widens the regex to `[CGDUW]` and adds the missing `conversations.open` step inside `_send_slack`. When `chat_id` starts with `U` or `W`, we call `conversations.open(users=[chat_id])` (cached per-process) and use the returned `D-id` for the actual `chat.postMessage` / `files_upload_v2` call.
- **Why we carry it:** Operator says "DM Jordan" — Dexter needs to take a U-id (from existing `slack_lookup_user.py` or Slack search) and send. Before this patch, the only working target was a D-id the operator had to look up by hand.
- **Tests:** `TestSlackUserIdToDmResolution` in `tests/tools/test_send_message_tool.py` — covers U/W → D resolution, D/C bypass, per-process cache, conversations.open API-error surfacing.
- **Drop when:** NousResearch independently lands the conversations.open conversion in upstream `_send_slack` (i.e. completes `75d3eaa0` themselves).

### 3. `[dexter-local]` feat(gateway): NO_REPLY sentinel suppresses delivery (re: #13248)

- **Commit:** `eb8fb68a` on `dexter`
- **Source:** [stevengonsalvez's proposal on issue #13248](https://github.com/NousResearch/hermes-agent/issues/13248#issuecomment-4393234015) (2026-05-07). 3-line drop-in.
- **What it does:** In `gateway/platforms/base.py::_process_message_background`, after `_unwrap_ephemeral`, if the agent's response is exactly `"NO_REPLY"` (post-`.strip()`), log and skip delivery. Zero impact when the token is absent.
- **Why we carry it:** Phase-2 hardening for the P1 empty-response retry loop in Slack group threads (issue #13248). With a prompt rule like *"in a non-@-mention group thread, respond with NO_REPLY when you have nothing to add"*, the model can decline a turn deterministically without triggering the gateway's silence-retry confusion.
- **Tests:** `tests/gateway/test_no_reply_sentinel.py` — 4 cases (exact match suppresses, trailing whitespace suppresses, normal response delivers, substring does NOT suppress).
- **Drop when:** NousResearch independently lands an equivalent sentinel (e.g. via a PR on #13248).

## Patches we considered and deferred

### PR #9395 — Slack approval-button + update-prompt + thread routing

[`shivasymbl/hermes-agent` `fix/slack-thread-routing`](https://github.com/shivasymbl/hermes-agent/tree/fix/slack-thread-routing). 1265 lines / 976 insertions / 6 files. The doc-described scope ("thread_ts on standalone sends") was much smaller than reality — the actual PR is a comprehensive approval-button + update-prompt + thread-engagement refactor in `gateway/platforms/slack.py`.

The 27-line piece we'd actually use (thread_id forwarding in `tools/send_message_tool.py`) is **already included** in PR #17348's second commit (`8af96da`), which is in patch 1 above. The remaining 1238 lines (approval buttons, update prompts, send-update watcher) are Phase 2+ features Dexter doesn't currently exercise.

Revisit when Phase 2 (private triage channel + approval flows) goes live. Until then: carrying this patch is pure maintenance debt.

## Rebase workflow

Use the dexter repo's `make fork-rebase` target (planned), which:

1. `git fetch upstream main`
2. `git rebase upstream/main` on `dexter`
3. For each `[dexter-pin]` commit, check whether the upstream PR has merged; if yes, mark the commit for removal and let `git rebase --interactive --autosquash` drop it (or drop manually)
4. Print the remaining patch inventory + a one-line trailer per patch
