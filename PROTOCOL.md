# Project Desk — working protocol

The operating protocol agents follow. See README.md for what this is and why.

Shared local coordination for the humans and the coding agents on one codebase. Python + SQLite + the official
MCP Python SDK (pinned maintenance line), with a dependency-free browser dashboard.

- Dashboard: http://127.0.0.1:7331/
- MCP endpoint: http://127.0.0.1:7331/mcp
- Health: http://127.0.0.1:7331/health
- Rules: `AGENTS.md`
- Claude handout: `CLAUDE-HANDOUT.md`
- Generated readable roster: `ROSTER.md`

## What is authoritative

SQLite at `data/desk.sqlite3` owns live status. AGENTS.md describes the working
protocol; ROSTER.md is an automatically refreshed read-only view. Earlier Markdown
rosters are archived before migration. Imported task status is reported history,
not fresh verification. Active imported claims stay reserved until the human owner reassigns
or closes them with a reason. Completed reports preserve their original evidence.

Use `<your-project-slug>` for this project across branches, clones and worktrees.
For another independent project, use a different lowercase project slug. File claims
are scoped to a project. `service:name` claims are global across projects.

## Working protocol

1. `register_session`: unique real-session name, agent (`codex` or `claude`), branch,
   absolute worktree, project. `project` is optional: when omitted, the desk resolves
   it from the worktree's `.project-desk.json`, its git main worktree, or a repo root
   registered on the dashboard. A different explicit project is refused. In strict
   mode (`PROJECT_DESK_STRICT=1`) an undeclared worktree is refused outright. The
   response includes `project`, and `hint` when the worktree declares nothing. Keep
   the returned session_key private; do not put it in messages, code, roster exports,
   or commits. Each session gets a different key.
2. `check_in`: pass session_key and the last returned event cursor (initially 0).
   Read active claims, the human owner's decisions, inbox, pauses, and handoff offers. If 100
   events are returned, continue from the returned cursor until caught up.
3. `claim_task`: provide title, literal relative paths/directories and next_step.
   To claim a queued task, also pass its task_id and exact existing resources.
4. `update_task`: always use the latest task version; update status and next step.
   Expanding scope passes the entire replacement resources list and checks overlap
   in the same transaction. A conflict rolls back the change; previous claims stay.
5. `send_message` / `acknowledge_message`: receipts are explicit and per session.
   A message may target one session, an agent kind, everyone, or the human owner. Acknowledgment
   means read, not authorization or task completion.
6. `offer_handoff` then `accept_handoff`: ownership remains with the sender until
   the named recipient explicitly accepts; the resource claim never disappears.
7. Complete with status DONE, summary and validation evidence. Record commit_ref
   and deployment separately. Completion releases claims. PAUSED and BLOCKED retain
   them. Only the human owner can lift a pause set through the dashboard.

Check in before edits, after long operations, at milestones, before a deployment,
and before ending a turn. No token-consuming autonomous agent loop runs in the
background. The service persists while agents are idle, but agents must check in
(or reload their tools) to learn about changes.

Lifecycle check-in hooks for Codex and Claude, enrollment, task discussions and
the limits of idle notifications are documented in [HOOKS.md](HOOKS.md).
Use `enable_notifications` with your own actual agent session UUID and existing
Desk key; this does not grant claims or manufacture read receipts.

## Crossover: work that spans projects

Isolation is the default; a crossover is the explicit exception.

- `send_message` recipients may also be a session id registered in another
  project, `<project>:all|claude|codex|human`, or a crossover id `x-...`. A
  cross-project message is stored in the receiving project (so receipts, hooks
  and the dashboard work unchanged) and carries `from_project`/`from_name` in
  the inbox. A cross-project message may carry a task_id only for a task in the
  receiving project that both projects share a crossover on.
- `list_peers` lists the projects, or one project's recent sessions and their ids.
- `start_crossover(task_id, invite, note)`: the owner of an open task invites
  other projects (slug) or sessions (id). Calling it again adds invitees.
- `join_crossover(crossover_id, next_step, resources | task_id)`: an invited
  project joins with a new claimed task on its own side, or links an open task
  it owns. One task per project per crossover; a task is in one crossover at most.
- Members (joined or invited) can read each other's task with `get_task_context`.
  `check_in(include=["crossovers"])` lists the open ones for your project.
- `sign_off_crossover(crossover_id, validation)`: the owner of a side's task
  records its validation. DONE on any crossover task is refused while another
  joined side has neither signed off nor finished; DONE with validation counts as
  that side's sign-off. The human's dashboard close is never blocked.

- Every crossover view carries `join_url` (`/x/<id>`, a markdown page with the
  exact calls) and `paste_line` for the human to paste into the other agent. The
  dashboard's **Cross over** task button (human action `crossover.start`) opens
  one without asking an agent; the task keeps its owner.
- `move_project` refuses to move a task that is in a crossover.

Data lives in side tables (`crossovers`, `crossover_members`, `message_links`);
the original tables keep their shape, so older code still runs on the database.

## Memory and coordination

- **Lessons** (shared memory): `remember(body, paths, tags, scope)` saves a short
  lesson; `claim_task`, `would_conflict`, `reopen_task` and `get_task_context`
  return the lessons whose paths overlap yours, most specific first. `recall`
  searches by words (every word, then any word), paths or tags. `scope="global"`
  shares a lesson with every project. `forget` archives one with a reason. The
  human adds or archives lessons on the dashboard's Lessons tab.
- **Journal**: every claim, update, resume, reopen and human action appends to the
  task's log; `log_progress` adds findings. `get_task_context` returns it, so a
  resumed or future session can pick the work up.
- **Resume**: `register_session` lists `resumable` earlier sessions of the same
  agent kind in the same worktree that still own open tasks. `resume_session`
  takes them over once that session has been silent 30+ minutes; its pending
  handoffs, queue places, open questions and approvals follow, and messages
  addressed to it reach the new inbox. The old identity is told.
- **Inbox triage**: every new message gets a kind (explicit `kind`, or inferred from
  a SHOUTED header like `DEPLOY DONE`). `check_in(include=["inbox_digest"])` lists
  unread first lines, addressed-to-you first; `read_messages` opens some;
  `acknowledge_inbox(kinds=[...])` clears broadcasts in one call (mail addressed to
  you only with `include_direct=true`). `counts` splits direct and broadcast.
- **Questions**: `ask` sends a question that stays open until the recipient replies
  with `send_message(reply_to=<question id>)`; `check_in(include=["questions"])`.
- **Approvals**: `request_approval(title, options, context)` puts a decision on the
  dashboard; the human's click records a decision note and messages the requester.
  Agents cannot decide approvals.
- **wait_for(timeout, resources, crossover_id)** blocks (≤300 s) until a message to
  you, an answer, a decision, a watched resource changing hands or a watched
  crossover changing.
- **Queue and deploys**: `queue_for(resource)` joins a FIFO queue for a held
  resource; when it frees, the head gets YOUR TURN and 15 minutes to claim before
  the next is told. `update_task(deployment="deployed", commit_ref=...)` on a task
  holding `service:X` records the deploy; `record_deploy` does it explicitly;
  `prod_state` reports the latest per service.
- **Crossover contracts**: `crossover_contract(crossover_id, body)` publishes a
  version (joined members only) and clears every sign-off; sign-offs record the
  version they matched.
- **Evidence**: `update_task` and `sign_off_crossover` take `evidence` checks
  (`command`, `exit_code`, `tests_passed`, `tests_failed`, `commit`, `output`). The
  board shows a task as *checked* or *failing* from them. The desk records what
  agents report; it does not run the commands.
- **Stale claims**: every ~10 minutes the desk messages the human once per episode
  about open tasks whose owner has been silent `PROJECT_DESK_STALE_HOURS` (6) hours;
  the dashboard lists them.
- **Reopen**: `reopen_task(task_id, reason, next_step)` lets an agent take a
  completed task in its project back, re-claiming its recorded paths atomically
  (refused on overlap). The previous owner and the human are told. Moving an
  OPEN task to another owner stays the human's call (Reassign) or a handoff.

- **Action items**: what an agent leaves for others when it finishes a task or a
  turn. `update_task(..., action_items=[...])` saves lines for the human, linked to
  the task; `add_action_items(items, task_id, assignee)` takes `human`, `agents`, a
  session id, or `<project>:human|agents`. They show as checkboxes on the dashboard
  (Action points, and on the task card). `resolve_action_item` ticks one done,
  dropped or open again: an agent may resolve items assigned to it or to agents, or
  ones it wrote. `check_in(include=["action_items"])` lists yours. **Rule:** notes
  an agent gives the human at the end of a task or turn ("left for you", "next",
  "waiting on") are also saved as action items.

All of this lives in new tables; the original tables keep their shape.

## Claim semantics and practical limits

Directories include descendants. Brackets in Next.js route names are literal.
Wildcards, absolute paths, and parent traversal are rejected. Treat symlinked paths
as the same logical repo path; this service does not scan repositories or resolve
filesystem aliases. Claim related repos under separate project IDs and shared live
services under the same service key. Use `service:<your-deploy-lane>` for app deploys.

Claims enforce coordination inside this service. They do not intercept arbitrary
shell edits or execute deployment commands. A session older than 15 minutes becomes
stale; its claims never silently expire. The service neither starts agents nor
resumes paused work. Peer notes do not override user instructions. Dashboard controls
are for the human owner, not an alternate API for agents to bypass owner or pause checks.

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
