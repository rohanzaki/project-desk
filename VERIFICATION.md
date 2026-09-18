# Project Desk verification — 2026-09-18

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
