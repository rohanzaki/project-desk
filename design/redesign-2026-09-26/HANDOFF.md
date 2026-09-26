# Project Desk dashboard redesign — dev handoff

Files
- `Project Desk App.dc.html` — wired, interactive prototype (the thing to build).
- `Project Desk Redesign.dc.html` — design board: 1a–1d explorations + 0a recreation of today's UI.

## Can it be wired? Yes.
The prototype runs on mock data shaped like `/api/state`, and every button calls the same
`/api/action` names that `static/app.js` already uses. Set the `apiBase` prop (Tweaks → Live wiring)
to switch from mock data to the live desk: it polls `GET {apiBase}/api/state?project=…` every 5 s
and POSTs `{project, action, data}` with header `X-Project-Desk: dashboard`.

Note: the server validates Host/Origin, so live mode only works when the page is served by the desk
itself (same origin, `apiBase = ""`). Porting the markup into `static/index.html` + `app.js` is the
intended path; no new backend endpoints are required for v1.

## UI → existing server action
| UI | action | data |
|---|---|---|
| Approval option / Other… | `approval.decide` | approval_id, decision, note? |
| Question "Yes, go ahead" / Reply | `message` | recipient, body, reply_to |
| Acknowledge / Ack all broadcasts | `ack` | message_id (one call each) |
| Action points Tick | `action.resolve` | item_id, status:"done" |
| Pause / Resume | `pause` / `resume` | task_id, version |
| Reassign | `reassign` | task_id, version, session_id, reason |
| Set priority | `priority` | task_id, version, priority |
| Close with note | `close` | task_id, version, summary |
| Reopen & reassign | `reopen` | task_id, version, session_id, next_step |
| Cross over | `crossover.start` | task_id, invite[], note |
| Add task | `create` | title, assigned_to, resources[], next_step |
| Comment (focus pane) | `message` | recipient=owner, body, task_id |
| Write message | `message` | recipient, body |
| Add / Archive lesson | `lesson.create` / `lesson.archive` | body, paths, tags, scope / lesson_id, reason |

## Attention queue ("Needs you") — derived client-side from /api/state
- Approvals: `approvals[status=pending]`
- Questions: `messages[kind=question, question_status=open, recipient=rohan]`
- Stale claims: `stale_claims` (prototype derives from sessions[stale] owning open tasks)
- Refused claims: `events[kind=claim.refused]` — **small backend gap**: needs a dismiss/resolve
  state (today it's only an event). "Queue it" maps to an agent-side `queue_for`; the human
  version needs a new dashboard action if you want it one-click.
- Action points: `action_items[status=open, assignee=rohan]`, grouped by task_id.

## Other gaps worth a ticket
- Agent lanes (timeline) uses mock bars. Build from `events` + `task_logs` (claim/done/deploy timestamps).
- `would_conflict` is computed client-side from open task resources; could call the real tool for parity.
- Undo is local-only in mock mode; in live mode it's hidden (server has no undo except untick action items).

## Keyboard
⌘K / Ctrl+K or `/` palette · J/K move in Needs you · 1–3 answer · E tick action points ·
↑/↓ move task selection · ⌘⏎ send comment / submit dialog · Esc close.

## Tokens (from static/style.css)
ink #182329 · paper #fbfaf5 / #f5f1e8 / #ece8de / bg #f1eee6 · line #d8d5ca / #c3c1b8 ·
blue #2456d6 · sage #3e745a · amber #c17b2c (text #8a5619) · red #a44339 ·
codex #176e76/#e1f0ef · claude #a55c36/#f4e7de · human #4e5ea2/#e7e9f6.
Type: Aptos/IBM Plex Sans stack for UI, IBM Plex Mono for paths/ids, Georgia for the wordmark (DESIGN.md).
