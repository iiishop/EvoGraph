# Agent stream and saved conversation

The default stream remains `full`. Both HTTP `AgentRequest` and the desktop bridge accept an optional `snapshot_mode: "compact-v1"` on the original request. Unknown modes are rejected before admission. The built-in frontend opts in once; it does not retry a possibly admitted turn to negotiate. Older servers can ignore the option and return their existing full snapshots.

- `started` confirms the admitted turn ID and mode and includes a full project view, including the saved user message and any cleared pending question
- Negotiated intermediate `graph_changed` frames omit `messages` and `events` and explicitly mark the project snapshot `snapshot_mode: "compact-v1"`. All planning fields are still present. Omission means preserve the current history, never clear it
- A compact stream emits `message_saved` after a provider round is persisted. Its `saved_message` is the canonical stored record, with stable ID and full content. The frontend checks envelope and record project identity, admitted turn, and message shape, then deduplicates by message ID
- `done` includes the ordinary full project view and the persisted terminal summary. Native cancellation retains its full terminal snapshot. HTTP interruption uses the exact admitted turn result followed by a best-effort project refresh
- The ordinary activity view remains bounded to its newest100 events. Exact turn-result recovery is separately indexed by project and turn ID and is not limited by that activity window

If terminal delivery and the exact turn-result read are temporarily unavailable, a later accepted project snapshot can reconcile the outcome only for the admitted project, creation identity, draft-entry owner and exact turn ID. Older or unrelated receipts never settle it. A reconciled submission cannot overwrite a newer turn’s status. Recovery metadata is retired only for that submission; current composer text and its original question provenance remain intact.

New assistant text is saved without the previous12,000-character truncation, including narration in tool-bearing, question and interrupted rounds. Interrupted history contains only text actually received from the provider; a partial answer does not establish successful completion. Saved user documents keep their typed reference identity and labels. Historical text already truncated or discarded by older versions cannot be reconstructed by this change. If a new narration write fails, the original cancellation or provider failure still takes precedence, the project lock is released, and a saved receipt carries an optional visible history warning. Ordinary narration-write failures produce a failed turn.

Full project reads and admission/terminal snapshots still include all saved messages. Compact mode reduces repeated history transfer and database reads; it does not virtualize old history, bound provider output, or eliminate the cost of a full reopen. No throughput or frame-rate guarantee is implied.

The SQLite migration adds a partial expression index over terminal events. Its JSON-validity guard accepts legacy free-text outcomes; unsupported receipt versions still return an unknown result rather than a fabricated success.

A pending project selection tracks intervening stream activity even when another project is on screen. If its captured read predates planning updates, canonical messages, or a recovered exact terminal receipt, the selection rereads under the same navigation owner instead of opening stale data. Reads are sequential and bounded to four attempts; newer navigation or project deletion supersedes them. Exhaustion reports a retryable loading error rather than claiming the old snapshot is current. This stores only pending-read metadata, not another history cache.

## Conversation reading and feedback

Expanding conversation history allocates a larger, bounded review area instantly. The review is its single scroll owner; question, input, attachments and Stop remain outside it. It does not auto-scroll a user who is reading earlier content. Project changes close the reading disclosure and reset its scroll position; draft ownership is unchanged.

Graph updates reuse one keyed, expiring notice per project instead of stacking repeated entrances. Notices are bounded at the top of the viewport, away from composer controls. Existing errors and saved terminal outcomes keep their own semantics. Changed-node markers and receipt links remain available.
