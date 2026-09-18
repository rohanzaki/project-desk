# Project Desk verification — 2026-09-18

## Notification and handoff release (1.1.0)

- Final full suite: **59 passed**, with the two dependency deprecation warnings
  described below. JavaScript syntax and `git diff --check` passed. Desktop and
  mobile screenshots inspected; bell foreground contrast corrected and unread
  state verified to survive reload until explicitly marked seen.
- Extended automated suite covers private Codex/Claude session bindings, native
  hook configuration preservation, reassignments, bounded event delivery,
  independent broadcast receipts, outage retries, concurrent deduplication,
  Stop-loop prevention and human pauses.
- Structured handoff checks cover immutable context, retained ownership until
  acceptance, project isolation, stale/superseded offers, human reassignment,
  pause/closure, and transaction rollback for oversized notifications.
- Isolated Playwright checks cover comments and replies retaining task context,
  explicit receiving-session selection, changelog broadcasts, bell updates and
  browser-local seen state separate from message read receipts.
- The standalone two-client MCP/browser regression passed: conflict refusal,
  explicit acknowledgments, ownership transfer, task completion, dashboard
  actions and mobile layout. All test clients use temporary databases.
- Native VS Code Codex app-server discovery reports all five Project Desk hook
  definitions enabled and trusted, with no discovery warnings/errors. Existing
  GSD SessionStart was retained. Claude settings retain unrelated handlers.
- Local service update from implementation commit `7415d84` reports health
  version `1.1.0`; all four new MCP tools are discoverable. A consistent private
  SQLite backup was taken first. The 11 sessions, 9 tasks, 7 messages and 49
  existing event IDs survived; database integrity is `ok`.
- Live `enable_notifications` authenticated the existing root session and
  returned its idempotent binding. A manual hook smoke check against the live
  service passed using a temporary copy of that binding. This deliberately did
  not mark the real IDE binding as executed or acknowledge any peer message.

Activation boundary: installation, trust and private enrollment do not prove an
already-open IDE process executed a hook. Actual peer acknowledgment remains
separate evidence. Lifecycle hooks run during matching client events; they do
not wake idle/closed conversations. Existing clients may require reload after
saving work. No peer process was restarted and no production PM2/app operation
was performed by this release.

The dashboard polls every three seconds while visible and no edit dialog is
open. Its recent view is bounded to 100 events, 200 messages and 100 handoff
briefs; older durable records remain in SQLite. Task-context reads return at
most 50 comments and 20 briefs. The bell is a browser-local seen marker, never
another agent's acknowledgment or permission to take its task.

## Original installation baseline

- 25 unit/integration tests passed. Covers eight concurrent claims, parent/child
  paths, project boundaries, global service claims, optimistic versions, owner
  enforcement, pause preservation, restart persistence, acknowledgment identity,
  paginated events, accepted handoffs, queued assignments, backup restore, imported
  claim preservation, and HTTP origin/host protection.
- Two independent MCP client connections completed discovery, registration, conflict
  refusal, messaging, acknowledgment, ownership transfer and task completion.
- Playwright exercised task creation, pause/resume, priorities, team messages,
  acknowledgment, decisions, reassignment, closure and filters. Desktop (1440px)
  and mobile (390px) screenshots inspected; no horizontal overflow or JS errors.
- Live service restart preserved session authentication, legacy tasks, installation
  task, shared inbox and event history. Startup backups exist and service is enabled.
- Codex config inspection shows project-desk enabled at the correct HTTP endpoint.
  Claude Code's own MCP connection check reports Connected.
- Five legacy task reports imported at migration; original text archived. Active
  imported claims retained. No assumption that an already running peer session has
  reloaded its tools or acknowledged the migration message.
- Browser test artifacts and test clients belong to a separate temporary database;
  they were not inserted into the live coordination database.

Known boundaries: cooperative claims do not intercept shell edits or deployment
commands. Agents must check in; the service does not automatically wake them.
Loopback-only dashboard is intended for the trusted local OS user. Backups are
local, not off-machine. Two dependency deprecation warnings occur in Starlette's
httpx test adapter; all assertions pass.
