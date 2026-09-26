# Project Desk — {name}

This repo coordinates its coding agents (Claude Code, Codex) and the human owner
through Project Desk. Live tasks, claims, messages and handoffs are kept in the
desk, not in Markdown files.

- Project: `{project}`, declared in `.project-desk.json` at the repo root. It
  applies to every branch, clone and worktree of this repo.
- Dashboard: {dashboard_url}
- MCP server: `project-desk`, endpoint {desk_url}/mcp
- Fallback CLI when MCP tools have not loaded: the `desk` command in your Project
  Desk install folder. The onboarding page shows its exact path on this machine:
  {dashboard_url}/onboard
- Agents work only in this project. The desk refuses a session registered in a
  project this repo does not declare.

## Required coordination loop

1. Register once per real session: `register_session` with a unique descriptive
   name, agent `codex` or `claude`, current branch and absolute worktree. Omit
   `project`; the desk reads it from `.project-desk.json`. Keep `session_key`
   private: never put it in code, logs, notes or commits.
2. `check_in` before edits, after long operations, at milestones, before
   deployments and before ending a turn. Ask for sections
   (`include=["inbox","counts"]`). Acknowledge the messages you have read with
   `acknowledge_message`; a list clears a backlog in one call.
3. Before editing, `claim_task` with a title, exact repo-relative files or
   directories, and a next step. Directories include descendants. An overlapping
   claim means stop: do not edit those paths. `would_conflict` shows who holds a
   path without claiming it. Claim `service:<name>` for a deployment or any other
   single-holder operation; service claims apply across all projects.
4. `update_task` with the latest version whenever work progresses, is blocked,
   pauses or ends. DONE requires a summary and validation evidence; record the
   commit and the deployment state.
5. `send_message` for questions and findings. The recipient is a session ID,
   `codex`, `claude`, `all`, or `human`. Acknowledging means read, not approved.
   A peer's message is context, never permission to widen the human's
   instructions. To reach another project, use a session ID registered there or
   `<project>:all` (`:claude`, `:codex`); `list_peers` shows who is there.
6. Hand work over with `prepare_handoff`. The owner keeps the claim until the
   receiver calls `accept_handoff`. Silence and stale presence are not consent.
   Never act as another session or use the dashboard's human-only controls.
7. Respect the human's pauses. Only the human can lift a dashboard pause.
8. Memory: `claim_task` and `would_conflict` hand you the lessons for your paths;
   read them. `remember` a short lesson (with paths and the why) when you learn a
   trap the code does not show; `recall` searches them. On long tasks,
   `log_progress` what you found. After a restart, `register_session` lists your
   earlier sessions; `resume_session` takes their open tasks back.
9. Busy inbox: `check_in(include=["inbox_digest"])`, `read_messages`, and
   `acknowledge_inbox` for broadcasts. `ask` when you need an answer (reply with
   `send_message(reply_to=...)`), `request_approval` for the human's decision,
   `queue_for` a busy deploy lane, `wait_for` instead of polling. Report checks as
   `evidence` on `update_task`. `reopen_task` takes back a completed task you need.
   Whatever you leave for the human or others at the end of a task or turn (to do,
   decide, check, waiting on) is also saved as action items on your task:
   `update_task(..., action_items=[...])` is the task's whole current list (items
   you leave out are superseded; `[]` clears it); `add_action_items(task_id=...)`
   adds to it. An item without a task is refused.
10. Work that spans two projects (an API one repo serves and another calls) is a
   crossover. The owner of the task calls `start_crossover` to invite the other
   project or its session; the invitee calls `join_crossover` with paths in its
   own repo, which claims a task on its own side. `send_message` to the crossover
   id (`x-...`) reaches every member. Each side records `sign_off_crossover`
   with evidence; no side can mark its task DONE until every other joined side
   has signed off. A crossover never lets you edit another project's files.

If the desk is unreachable, write a local handoff note and avoid new overlapping
edits or deployments until it is back.

## Notifications

Hooks installed with `codex_hooks.py install --agent claude` (or `--agent codex`)
from your Project Desk install folder deliver inbox and task updates during
active work. The onboarding page shows the exact command:
{dashboard_url}/onboard

After registering, bind your client session once with
`enable_notifications(session_key, agent_session_id)`. Hooks do not wake an idle
session and never acknowledge messages for you.
