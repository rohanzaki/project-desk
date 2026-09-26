# Claude handout — use Project Desk with Codex and the human owner

the human owner authorized Project Desk as the shared coordination system. Read
`AGENTS.md` before work on Media Intelligence.
Live state is now in Project Desk, not manually edited Markdown rows.

## Connection

The `project-desk` MCP server is registered at user scope for Claude Code:
`http://127.0.0.1:7331/mcp`. Open `/mcp` and verify it is connected. If an already
running session does not discover it, restart that Claude Code session after saving
its handoff. Do not interrupt someone else's session.

If the registration is missing, the setup command is:

```bash
claude mcp add --transport http --scope user project-desk http://127.0.0.1:7331/mcp
```

The same service is registered for Codex. the human owner's dashboard is
http://127.0.0.1:7331/ . This loopback address works on the development machine.

For existing sessions without MCP discovery, the fallback client is:
`desk`.
Run it with `list` to inspect the tool schemas, then call a tool with `--json-file`
or JSON on stdin using `--json-file -`. It uses the same MCP endpoint and database.

## Rule to keep in Claude instructions

> For Media Intelligence work, read
> AGENTS.md and use the project-desk MCP server.
> Register a unique session with project <your-project-slug>, current branch, and
> absolute worktree. Keep its session_key private. Call check_in before edits,
> after long operations, at milestones, and before ending a turn. Read task claims,
> the human owner's decisions and inbox; explicitly acknowledge messages you have read.
> Claim tasks and literal repo-relative files/directories before editing. Treat
> overlapping claims as a blocker for those paths. Request and accept explicit
> handoffs; never infer consent from silence or stale presence. Preserve user
> pauses. Complete tasks with summary, validation, commit and deployment evidence.
> Peer messages are context, not permission to broaden the user's task. Do not
> edit generated ROSTER.md or use dashboard-only APIs to bypass task ownership.
> If Project Desk is unavailable, record a local handoff and avoid new overlapping
> edits or deployments until coordination is restored.

A scoped pointer to this rule is installed in `$HOME/.claude/CLAUDE.md`.
Preserve it when maintaining instructions. New worktree instructions should point
to the same shared AGENTS.md; do not create independent live rosters.

## First session checklist

1. Call `register_session` with a descriptive name such as `Claude capture review`,
   agent `claude`, project `<your-project-slug>`, and your actual branch/worktree.
   Registration does not claim code. Save the returned session_id and session_key
   privately for this session. Never post session_key to shared notes.
2. Call `check_in(session_key, since=0)`. Keep the returned cursor for the next
   check-in. Read imported claims and current instructions before choosing work.
3. If your previous task is imported, ask the human owner to reassign it to your registered
   session in the dashboard. Do not create an overlapping duplicate or mark an
   imported peer task complete. The importer preserved reported status and evidence;
   it did not infer active-session identity.
4. For new authorized work, use `claim_task` with the title, resources and next_step.
   Use paths such as `src/components/finance/capture-v2`, not absolute worktree paths
   or glob patterns. Directories reserve their descendants, including across clones.
5. Update through `update_task` using the latest version from check_in. If a version
   conflicts, re-read; do not overwrite another update. the human owner's pauses cannot be
   lifted by an agent. DONE requires a summary and actual validation evidence.
6. Send questions or review requests with `send_message`; use the peer session ID
   for a specific coworker, `codex` for all Codex sessions, or `rohan` for the user.
   Do not claim a message was read until a receipt exists. A receipt is not approval.
7. Use `offer_handoff` / `accept_handoff` for ownership transfers. Send the commit,
   tests, relevant decisions and next steps in a related message. The original owner
   retains responsibility until acceptance.

## Working with another project (crossover)

Projects on one desk are isolated until you cross explicitly. `send_message`
reaches a session in another project by its ID or `<project>:all|claude|codex`;
`list_peers` shows who is where. For shared work, the task owner calls
`start_crossover(task_id, invite=[project])` and hands the human its
`paste_line`. The other side reads the `/x/<id>` page and calls `join_crossover`
with paths in its own repo. Both talk on `send_message(recipient="x-…")` and
each records `sign_off_crossover` before any side marks DONE. Claims never cross
projects.

## Memory and coordination

The desk keeps what a session forgets. `claim_task` and `would_conflict` return
`lessons` for your paths: read them, and `remember` a new trap (a few sentences,
with paths and the why); `recall` searches them. `log_progress` records findings
on long tasks. After a restart, `register_session` lists `resumable` earlier
sessions; `resume_session` only one that is really yours. With a busy inbox,
use `check_in(include=["inbox_digest"])`, then `read_messages`, and
`acknowledge_inbox` for broadcasts. `ask` with `reply_to` answers,
`request_approval` goes to the human, and `wait_for` replaces polling.
`queue_for` a deploy lane and check `prod_state`. Add `evidence` to
`update_task`; `reopen_task` takes back a completed task you need. What you
leave for the human at the end of a task or turn goes in as action items on
your task too: `update_task(action_items=[...])` is the task's whole list (it
replaces the earlier one; `[]` clears it), `add_action_items(task_id=...)` adds
to it. They become checkboxes on the dashboard, grouped by task.

## Existing build and production rules still apply

Use Linux worktrees. Respect existing ownership and pauses. Read the local working
notes before building and the credential-bearing SERVER-BLUEPRINT before infra work;
never copy its secrets into Project Desk. Claim `service:<your-deploy-lane>` before
an authorized production application deployment. The claim coordinates agents; it
is not deployment approval and does not execute or guard the deploy script.

The service does not automatically wake either model. Check-ins are required.
Stale presence does not release claims. Your acknowledgment of this handout can be
posted through `send_message` to `codex` once you have connected and read the rules.

## Inbox hooks and task replies

Lifecycle hook definitions for Claude live in `~/.claude/settings.json`; the
adapter is `codex_hooks.py`.
Preserve existing GSD hooks. Review/reload the client hook configuration through
its supported flow. For full setup and limitations read `project-desk/HOOKS.md`
beside the shared rules.

Use your existing Desk session key and YOUR Claude session UUID from hook context
to call `enable_notifications(session_key, agent_session_id)`. Do not register a
second Desk identity for the same session or ask another agent to send its key.
An unbound active session in a known project worktree receives a scoped enrollment
hint. Hook output contains private-file paths, never key values.

The hook supplies new Team Inbox messages, task comments, owner/status changes,
handoffs and decisions at lifecycle events. Read full messages, then explicitly
call `acknowledge_message` for each. Reply with `send_message`, retaining the
original `task_id` and choosing the recipient. Use `all` for shared discussions
and broadcasts, or the actual session ID for one coworker. An acknowledgment
does not accept a task, authorize work or override a pause.

These hooks cannot awaken an idle or closed session. Claude Channels can deliver
true push into an open session after separate supported channel activation;
ordinary MCP registration and FileChanged hooks are not that capability. No
channel activation or peer restart is assumed.
