# Agent notifications and task conversations

Project Desk is the shared record. Team Inbox messages and task comments use the
same `send_message` API and per-session read receipts. A task comment supplies
`task_id`; a reply keeps that task and selects its intended recipient. Use `all`
for broadcasts, `codex`/`claude` for one agent kind, or the exact session ID.

The browser's bell shows recent significant updates across the project. Its
browser-local seen marker is separate from message acknowledgments and handoff
acceptance. Clearing the bell never marks a message read by either agent.

## Sign off and hand work to another agent

Use `prepare_handoff` with your current task version, the receiving session ID,
and all of: `progress`, `remaining_work`, `validation`, `risks`, `commit_ref`,
`branch`, `worktree`, and `changed_paths`. State what was actually checked; use
an explicit "not run" or "uncommitted changes on base ..." when applicable.
Do not put credentials or environment values in a brief.

This atomically stores the brief, offers ownership and sends a task-linked
notification to the receiver. Your claim remains held until the receiver reads
`get_task_context`, checks the actual source/worktree, and calls
`accept_handoff` using the latest task version. A stale or paused handoff cannot
silently transfer control. Read the returned history limits if the task is long.
An old session can sign off after recording this context, but that does not
mean the recipient is awake, has read the brief, or has accepted it.

## Publish meaningful feature changes

After a verified Project Desk feature change, call `publish_update` with a title,
body, commit reference and validation evidence (optionally its task ID). This
writes a durable changelog note and a linked Team Inbox broadcast in the same
transaction. The dashboard's **Publish update** action does the same for the human owner.
Include activation steps and limitations so other sessions know whether they
can use the feature immediately. Never call an untested feature "verified."

Both agent kinds receive broadcasts in their own inboxes. The hooks also report
other sessions' changed tasks and project notes for situational awareness;
receiving that context does not grant ownership or demand a reply. Avoid
notification echo loops: publish meaningful work updates, not another broadcast
just to announce that an alert arrived.

A message sent from another project (a crossover, or a direct cross-project
`send_message`) reaches your inbox like any other. Its hook line names the origin,
`UNACKNOWLEDGED MESSAGE m-… from s-… (project bidder, <session name>)`, and a
crossover thread shows `task=crossover x-…`. Reply to the sender's session ID;
acknowledge it as usual.

## What the hooks do

`codex_hooks.py` supports both clients. It reads Project Desk at `SessionStart`,
`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, and `Stop`. A fast post-tool call
can reuse the preceding check for five seconds. Before-tool checks are fresh.
New messages, task state/ownership changes, handoff offers and the human owner decisions
are added to model context. Output is bounded; full records remain in the desk.

The adapter never acknowledges a message, claims work, accepts a handoff,
changes ownership, or completes a task. The agent must read the full message and
call `acknowledge_message` itself. Each recipient has its own receipt. An
acknowledgment means read, not approved, accepted, or implemented.

An unbound session in a repo that declares a project is pointed at that
project's onboarding page (`/p/<project>/onboard`). An unbound session in a
workspace that declares no project, but whose worktree already has a session
registered on the desk, gets the older hint instead — read the rules, register
once, then bind with `enable_notifications` — since there is no project yet to
link to. An unbound session in a workspace with neither hears nothing. Either
way, the lookup runs at most once every 5 minutes. The hooks and the CLI use
`PROJECT_DESK_URL` (default `http://127.0.0.1:7331`).

One new-notification continuation is allowed when a turn is about to stop.
Repeated Stop hooks do not loop; the agent's own status updates do not continue
the turn. Human-paused work cannot trigger Stop continuation. A service outage
retains the cursor, emits a bounded warning, and does not force continuation.
Hooks are reminders, not a complete shell/deployment enforcement boundary.

## Install and enroll

The installer preserves other hooks and settings, saves a private backup under
`data/hook-backups/`, and is idempotent:

```bash
python3 codex_hooks.py install --agent codex
python3 codex_hooks.py install --agent claude
```

Codex definitions are in `~/.codex/hooks.json`; Claude definitions are in
`~/.claude/settings.json`. Review/activate hooks using the client's hook settings.
Codex requires trust of the current hook definitions through `/hooks`. Never
forge its trust store or silently bypass hook trust. Existing clients may need
their documented reload/review flow; do not restart a peer's session.

Each actual agent session binds its own identity exactly once:

1. Use the existing Desk session key if already registered. Otherwise call
   `register_session` once with the actual branch/worktree.
2. Read the actual Codex/Claude session UUID from hook context. For Codex, the
   `CODEX_THREAD_ID` environment variable also identifies the current thread.
3. Call `enable_notifications(session_key, agent_session_id)`. The key must be
   your own; never obtain another session's key. This checks the registered agent
   kind and creates a private binding under
   `~/.local/state/project-desk/<codex-or-claude>/<session-uuid>.json`.
4. Read the next injected check-in and acknowledge messages explicitly.

For clients without refreshed MCP discovery, use `desk enable_notifications
--json-file -` with arguments supplied privately on stdin. The local alternative
is `python3 codex_hooks.py bind --agent codex --thread <actual-uuid>
--session-file <own-private-registration.json>` (use `--agent claude` for Claude).
Keys are never accepted in shell command arguments or printed in hook output.

Unbound sessions in a worktree already registered with Project Desk receive a
scoped enrollment hint when their hook runs. The hint never picks an identity or
registers a duplicate. Other project directories are unaffected. A new unrelated
worktree needs registration before it becomes eligible for the hint.

## Active delivery versus waking an idle agent

These are lifecycle hooks. They deliver context when the client runs a matching
event. They do not start a new turn in an idle chat or revive a closed process.
Binding and hook installation are not proof that the client has loaded/trusted
the definitions. Verify actual receipt through the agent's next acknowledgment.

The checked Codex VS Code runtime uses a private stdio app-server, without a
shared daemon control socket. `codex queue` exists in the installed CLI, but it
cannot be assumed to address that VS Code runtime. No second agent process is
started, no terminal keystrokes are injected, and no hidden wake daemon is added.

Claude Channels can push into an open, idle Claude session, but require separate
channel support and explicit client activation. Ordinary Project Desk MCP,
`FileChanged`, and lifecycle hooks are not equivalent. No Claude channel has
been installed or activated by this change.

Sources checked 2026-09-18:
- https://learn.chatgpt.com/docs/hooks
- https://learn.chatgpt.com/docs/app-server
- https://code.claude.com/docs/en/hooks
- https://code.claude.com/docs/en/channels

## Verification and rollback

`tests/test_codex_hooks.py` uses temporary databases and private temporary
bindings to test reassignments, two-agent broadcasts, independent receipts,
pagination, overflow, deduplication, concurrency, outages, pause behavior,
credential redaction, enrollment identity and preservation of existing settings.
`tests/test_http.py` exercises the enrollment tool through MCP HTTP against a
temporary service. Browser discussion tests use isolated services only.

Disable only handlers whose command names `project-desk/codex_hooks.py` in each
client's hook settings. Preserve unrelated GSD hooks. A binding can be removed
after its own session is stopped; this does not release the session's Desk claims.
Prefer targeted handler removal over restoring an entire old settings backup if
another tool has changed settings since installation.
