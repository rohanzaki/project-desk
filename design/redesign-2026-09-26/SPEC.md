# Project Desk dashboard v2 — spec

Rohan, 2026-09-26 23:35 PKT: "do all that you mentioned … make sure desk users on live
won't be disturbed". Design source: `Project Desk App.dc.html` (this folder), tokens in
`HANDOFF.md`. This spec adds what the design lacks for the real desk.

## Goals

1. The human owner sees what needs him first, across every project, and acts in one click
   or one key.
2. Nothing the current dashboard can do is lost.
3. Agents are unaffected: MCP tools, hooks, rules and `/api/state` do not change.
4. The page stays fast with real volume: 257 tasks, 101 sessions, 200 messages in CMU.

## Not disturbing the live desk

- The new UI is built at `/v2`; `/` stays the current dashboard until cutover.
- Server work is additive: a new module `desk_board.py` (`BoardMixin`), new read endpoints,
  new human actions, new tables created with `CREATE TABLE IF NOT EXISTS`. No change to the
  MCP tools, `codex_hooks.py`, `/api/state` or any existing table's columns.
- Development runs against a copy of the DB on port 7399. One announced restart ships it
  (announce to every board + VS Code peers incl. Bidder, back note after).
- Cutover (after Rohan approves `/v2`): `/`, `/p/{slug}`, `/projects` serve v2; the old page
  stays at `/classic`.
- Every new endpoint is cheap: bounded queries, no per-row N+1, so hooks never wait on it.

## Server contracts (all GET are JSON; `project` defaults as `/api/state` does)

### GET /api/board?project=P&done_days=7
The page's main feed. Returns `ETag: "<global max event seq>-<unix minute>"`; answers 304
with no body when the request's `If-None-Match` equals it (the client sends it by hand).
Keys:
- `project`, `generated_at`, `version` (the ETag value)
- `sessions`: non-imported sessions seen in the last 7 days OR owning an open task:
  `id,name,kind,branch,worktree,last_seen,stale` (stale = last_seen > 15 min ago, as today)
- `tasks`: every non-DONE task plus DONE tasks updated in the last `done_days`; same fields
  as `store.task()`. `tasks_done_total`, `tasks_done_shown` counts.
- `evidence` (summary per returned task, as `/api/state`)
- `messages` (last 100, decorated exactly as `/api/state`: kind, reply_to, question_status,
  acknowledgments, from_project…), `outgoing_messages` (last 30)
- `notes` (last 60), `lessons`, `approvals`, `open_questions`, `queues`, `prod`,
  `shared_locks`, `crossovers`, `stale_claims`, `action_items` — same shapes as `/api/state`
- `handoff_briefs`: offered (pending) only
- `refusals`: open refusals (see below)
- `snoozes`: `{key: until}` active for this project

### GET /api/task?project=P&id=T
`{task, journal (last 80 task_log rows, oldest first, with author_name), messages (every
message with task_id=T, decorated + acknowledgments), action_items (all statuses),
evidence (full check rows), lessons (those whose paths overlap the task's resources),
handoff_briefs (for T), crossover (the crossover T is in, or null)}`.

### GET /api/attention?projects=all|p1,p2&include_snoozed=0
Everything waiting on the human, across projects (active = not archived). Items:
`{key, project, kind, title, meta, task_id, task_title, who:{id,name,kind}, created,
snoozed_until?, …kind fields}`. Kinds and keys:
- `approval` (`approval:<id>`): pending approvals; + `approval_id`, `options`.
- `question` (`question:<message_id>`): `questions` rows with status open whose recipient
  is `rohan`/`human`; + `message_id`, `body`.
- `stale` (`stale:<task_id>`): `_stale()` rows; + `resources`, `owner_last_seen`.
- `refused` (`refused:<refusal_id>`): open refusals; + `refusal_id`, `resources`,
  `holder_task_id`, `holder_session`, `holder_name`.
- `action` (`action:<project>:<task_id or none>`): open action items assigned to the human,
  grouped by task; + `item_ids`, `bodies`.
- `blocked` (`blocked:<task_id>`): BLOCKED tasks not already covered by an approval or
  question on the same task, whose next_step matches
  `\b(rohan|you|your|human|owner|decision|decide|approve|approval|go.?ahead|ok to|confirm)\b`
  (case-insensitive); + `next_step`.
Sorted: approval, question, blocked, stale, refused, action; then newest first. Response also
has `counts: {project: n}` and `total`. Snoozed items are left out unless include_snoozed=1.

### GET /api/digest?project=P&since=ISO
What changed since the human last looked (≤20 rows per list, plus counts):
`{since, until, done:[{task_id,title,owner_name,summary,updated}], deploys:[{service,
commit_ref,summary,session_name,created}], claimed:[…new tasks], questions:[…opened],
approvals:[…requested], decisions:[notes kind decision/changelog], messages_to_you:n}`.

### GET /api/search?project=P&q=text
Case-insensitive `LIKE` over tasks (title, next_step, summary, resources, id), messages
(body), notes (body), lessons (body, paths). ≥2 chars. ≤10 per kind with a 160-char
`snippet` around the match. `{tasks, messages, notes, lessons}`.

### GET /api/events?project=P&before=SEQ&limit=50
Activity with readable text: each event gets `actor_name` and `text` (a one-line
description per kind, e.g. `task.claimed` → "claimed t-… <title> (<paths>)",
`deploy.recorded` → "deployed <service> at <commit>", `message.acknowledged` →
"read m-…"; unknown kinds → the kind + compact data). `before` pages backwards.

### GET /api/lanes?project=P&hours=12
Agent timeline built from events: `{start, end, sessions:[{id,name,kind,branch,stale,
bars:[{task_id,title,start,end,kind:run|done|blocked|stale}], marks:[{t,kind:deploy|
question|approval|refused|decision|handoff,text,task_id}]}]}`. A bar runs from the task's
`task.claimed` (or `start` if earlier) to its DONE/close (or now). Only sessions with a bar
or mark in the window.

### Refused claims (new)
`store.claim` records a refusal when `claim_resources` refuses an overlap: table
`refusals(id, project, session_id, title, resources, holder_task_id, holder_session,
holder_project, created, status open|dismissed|queued|handoff_asked, resolved,
resolved_by)` and event `claim.refused`. It is written in its own transaction after the
refused one rolls back, and the Conflict is re-raised unchanged (the agent sees exactly
the same error as today).

### New human actions (POST /api/action)
- `ack.inbox` `{kinds?}` → acknowledge every unread broadcast (`all`) for the human in one
  call; `{acknowledged:n}`.
- `answer` `{message_id, body, also_decision?}` → reply to the asker (reply_to set, so a
  question is marked answered) and, if also_decision, save a decision note so every
  agent's hook shows it.
- `refusal.dismiss|refusal.queue|refusal.handoff` `{refusal_id, note?}` → queue puts the
  refused session in `resource_queue` for the first refused resource; handoff messages the
  holder's session. All mark the refusal and add an event.
- `snooze.set` `{key, until}` / `snooze.clear` `{key}` → table `snoozes(project, key,
  until, created)`.

### Desk feature requests (Rohan 2026-09-26 23:54)
Agents propose improvements to the desk itself; nothing is built before the human approves.
- Table `desk_requests(id 'dr-…', origin_project, author, title, why, proposal, status
  proposed|approved|rejected|in_progress|done|withdrawn, created, decided, decided_by,
  decision_note, volunteer, volunteer_project, task_id, updated)` and
  `desk_request_support(request_id, session_id, project, note, created)`. Global: every
  project sees every request (the desk is shared).
- MCP tools (additive): `request_desk_feature(title, why, proposal)` → the request + up to 5
  similar open ones (so the agent can support instead of duplicating);
  `list_desk_requests(status?)`; `support_desk_request(request_id, note)`;
  `volunteer_desk_request(request_id)` → refused unless status is approved and nobody has
  it; atomically sets in_progress, the volunteer, and claims a task in the volunteer's
  project titled "Desk request dr-…: <title>" on `service:project-desk-dev` (one desk change
  at a time), returning the checklist (tests green, dry run on a DB copy, announce the
  restart to every board AND VS Code peers incl. Bidder, back note after, update the rule
  MD files if agents' routine changes). `withdraw_desk_request(request_id, reason)` by its
  author while proposed.
- Human actions: `desk_request.approve|reject` `{request_id, note}`; approve broadcasts
  "DESK REQUEST APPROVED dr-…: <title>. Volunteer with volunteer_desk_request('dr-…')" to
  every active project. Reject tells the author.
- The linked task going DONE shows the request as done (computed on read).
- Attention kind `desk_request` (`desk_request:<id>`) for proposed requests: Approve /
  Reject / Snooze. `/api/board` and a `GET /api/desk-requests?status=` list them.
- Rules (AGENTS.md, rules.md): request instead of editing the desk; check the list and
  support an existing request; volunteer only for approved ones.

## Page (static/v2)

Stack: Preact + htm "standalone" ES module and IBM Plex fonts, both vendored under
`static/v2/vendor` (CSP is `'self'` only: no CDN, no inline scripts, styles via classes or
the CSSOM). Polls `/api/board` every 4 s with If-None-Match and `/api/attention` every 8 s;
pauses while hidden.

Layout follows the design: 52 px top bar (project switcher, palette, prod chip, live state,
bell with the attention count, avatar), left nav, main, task pane at ≥1280 px (a drawer
below that), single column + bottom nav on phones (<760 px).

Views:
- **Command center**: "Since you last looked" strip (digest since the last visit, stored
  per browser); **Needs you** across all projects with project tags, tabs per kind, keys
  J/K, 1–3, E, S (snooze menu: 1 h, 4 h, tomorrow 09:00); **On the desk** (live sessions +
  stale ones holding claims; "N older sessions" folded); **Work** (grouped by status).
- **Tasks**: status tabs, owner filter, search, pinned first, DONE limited to 7 days with
  "Show all done" (loads `done_days=3650`).
- **Task pane**: header actions (pause/resume, reassign, priority, cross over with project
  choice, close, reopen), next step, claims, evidence, lessons on its paths, action points
  (tick, clear, restore), handoff brief (pending owner), crossover state and join link,
  journal, discussion with comment box (⌘⏎).
- **Action points**: the full panel (for you / for agents / everything / recently done),
  grouped by task, Clear per task, "Clear…" (finished, older than 3 days, unlinked, shown),
  Add.
- **Decisions & notes**: list (decision / changelog / note), Add decision, Publish update.
- **Inbox**: for you / unread / everything / sent to other projects; reply, acknowledge,
  "Acknowledge all broadcasts" (one `ack.inbox`), write message.
- **Claims & locks**: held paths, would-it-conflict (client overlap with the server's
  rules), service lanes from `prod` + `queues`, locks held in other projects.
- **Agent lanes**: `/api/lanes`.
- **Lessons**: list, add, archive.
- **Activity**: `/api/events` with text and paging.
- **Projects**: overview of every project (open tasks, needs-you count), create/update,
  settings, connect agents, announce restart.
- **Palette (⌘K, /)**: go to views, actions on the selected task, create, open tasks,
  agents, and server search over messages, notes and lessons.
- Browser notifications for new "Needs you" items (opt-in from the bell).

## Testing

- Server: `tests/test_board.py` — each endpoint's shape and filters, ETag/304, attention
  kinds and snoozing, refusal recording (the agent still gets the same Conflict text),
  the new actions, lanes from events, digest windows; the whole suite stays green.
- Page: headless Chrome screenshots of every view against the design at 1600 px, 1100 px
  and 390 px, on a copy of the live DB; drive the main flows (answer, tick, clear, snooze,
  pause, comment) against the copy and check the DB after each.
