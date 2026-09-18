# Project Desk

Shared local coordination for the owner, Codex and Claude. Python + SQLite + the official
MCP Python SDK (pinned maintenance line), with a dependency-free browser dashboard.

- Dashboard: http://127.0.0.1:7331/
- MCP endpoint: http://127.0.0.1:7331/mcp
- Health: http://127.0.0.1:7331/health
- Rules: `/path/to/mi-coordination/AGENTS.md`
- Claude handout: `/path/to/mi-coordination/CLAUDE-HANDOUT.md`
- Generated readable roster: `/path/to/mi-coordination/ROSTER.md`

## What is authoritative

SQLite at `data/desk.sqlite3` owns live status. AGENTS.md describes the working
protocol; ROSTER.md is an automatically refreshed read-only view. Earlier Markdown
rosters are archived before migration. Imported task status is reported history,
not fresh verification. Active imported claims stay reserved until the owner reassigns
or closes them with a reason. Completed reports preserve their original evidence.

Use `media-intelligence` for this project across branches, clones and worktrees.
For another independent project, use a different lowercase project slug. File claims
are scoped to a project. `service:name` claims are global across projects.

## Working protocol

1. `register_session`: unique real-session name, agent (`codex` or `claude`), branch,
   absolute worktree, project. Keep the returned session_key private; do not put it
   in messages, code, roster exports, or commits. Each session gets a different key.
2. `check_in`: pass session_key and the last returned event cursor (initially 0).
   Read active claims, the owner's decisions, inbox, pauses, and handoff offers. If 100
   events are returned, continue from the returned cursor until caught up.
3. `claim_task`: provide title, literal relative paths/directories and next_step.
   To claim a queued task, also pass its task_id and exact existing resources.
4. `update_task`: always use the latest task version; update status and next step.
   Expanding scope passes the entire replacement resources list and checks overlap
   in the same transaction. A conflict rolls back the change; previous claims stay.
5. `send_message` / `acknowledge_message`: receipts are explicit and per session.
   A message may target one session, an agent kind, everyone, or the owner. Acknowledgment
   means read, not authorization or task completion.
6. `offer_handoff` then `accept_handoff`: ownership remains with the sender until
   the named recipient explicitly accepts; the resource claim never disappears.
7. Complete with status DONE, summary and validation evidence. Record commit_ref
   and deployment separately. Completion releases claims. PAUSED and BLOCKED retain
   them. Only the owner can lift a pause set through the dashboard.

Check in before edits, after long operations, at milestones, before a deployment,
and before ending a turn. No token-consuming autonomous agent loop runs in the
background. The service persists while agents are idle, but agents must check in
(or reload their tools) to learn about changes.

Lifecycle check-in hooks for Codex and Claude, enrollment, task discussions and
the limits of idle notifications are documented in [HOOKS.md](HOOKS.md).
Use `enable_notifications` with your own actual agent session UUID and existing
Desk key; this does not grant claims or manufacture read receipts.

## Claim semantics and practical limits

Directories include descendants. Brackets in Next.js route names are literal.
Wildcards, absolute paths, and parent traversal are rejected. Treat symlinked paths
as the same logical repo path; this service does not scan repositories or resolve
filesystem aliases. Claim related repos under separate project IDs and shared live
services under the same service key. Use `service:mintel-app-deploy` for app deploys.

Claims enforce coordination inside this service. They do not intercept arbitrary
shell edits or execute deployment commands. A session older than 15 minutes becomes
stale; its claims never silently expire. The service neither starts agents nor
resumes paused work. Peer notes do not override user instructions. Dashboard controls
are for the owner, not an alternate API for agents to bypass owner or pause checks.

## Run and manage

```bash
systemctl --user status project-desk
systemctl --user restart project-desk
journalctl --user -u project-desk -n 50 --no-pager
```

The service is enabled for the user's systemd session. It binds only to loopback;
this is a trusted single-user workstation service, not a multi-user internet app.
Host/origin checks prevent cross-origin browser requests; no permissive CORS.
Dashboard mutation requires its custom header. Agent keys enforce session ownership
within the tool API, not isolation from other processes owned by this OS user.

Install dependencies in `.venv` with `pip install -r requirements.lock`. To run
manually, stop the user service first, then `.venv/bin/python server.py`.
Environment overrides: PROJECT_DESK_PORT, PROJECT_DESK_DB, PROJECT_DESK_ROSTER.

The `desk` executable is a fallback MCP client for sessions that have not reloaded:
`./desk list` prints schemas. `./desk check_in --json-file /path/to/private-args.json`
calls the tool. `--json-file -` reads JSON from stdin. Keys should live only in a
private local session file (mode 0600) when persistence is needed, never a shared note.

## Backups and restore

A consistent SQLite backup is made at startup and hourly in `data/backups/`; the
most recent 48 are retained. This is local restart/recovery protection, not an
off-machine disaster backup. The database and backups contain shared project notes
and are ignored by git. Session keys are stored as hashes, never plaintext.

To restore: stop the service, preserve `data/desk.sqlite3` together with any `-wal`
and `-shm` files in a dated recovery directory, then copy a chosen backup to
`data/desk.sqlite3` (mode 0600). Start the service and inspect task ownership before
resuming. Do not overwrite a live SQLite database.

## Verification

Unit/integration tests: `.venv/bin/python -m pytest -q`.
`tests/e2e.py` expects an isolated test service on port 7332 and a clean test DB.
It uses two independent real MCP client connections and Playwright to exercise
conflicts, receipts, handoffs, completion, and the dashboard. Never run it against
the live coordination database. Screenshots are written to `artifacts/`.

Protocol reference: https://github.com/modelcontextprotocol/python-sdk/tree/v1.x
Client setup: https://learn.chatgpt.com/docs/extend/mcp and
https://code.claude.com/docs/en/mcp
