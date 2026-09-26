"""What an agent needs to join a project: rules, declaration and setup kit.

Pure text rendering. Nothing here touches the database; the only file read is
rules.md (or a project's own rules file)."""
import io
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BEGIN = '<!-- project-desk:begin v1 -->'
END = '<!-- project-desk:end -->'
KIT_FILES = ('.project-desk.json', 'AGENTS.project-desk.md', 'CLAUDE.project-desk.md', 'SETUP.md')


def _context(project, desk):
    desk = desk.rstrip('/')
    return {'project': project['slug'], 'name': project['name'], 'desk_url': desk,
            'dashboard_url': f"{desk}/p/{project['slug']}", 'desk_cli': str(ROOT / 'desk'),
            'desk_root': str(ROOT)}


def _fill(template, values):
    # Explicit replacement, not str.format: rules text may contain literal braces.
    for key, value in values.items():
        template = template.replace('{' + key + '}', value)
    return template


def _indent(text):
    return '\n'.join(('    ' + line) if line else '' for line in text.splitlines())


def render_rules(project, desk):
    own = project.get('rules_path') or ''
    if own and Path(own).is_file():
        return Path(own).read_text()
    return _fill((ROOT / 'rules.md').read_text(), _context(project, desk))


def declaration(project, desk):
    return json.dumps({'project': project['slug'], 'name': project['name'],
                       'desk': desk.rstrip('/')}, indent=2) + '\n'


def agents_block(project, desk):
    return f'{BEGIN}\n{render_rules(project, desk).strip()}\n{END}\n'


def claude_block(project, desk):
    v = _context(project, desk)
    return (f"{BEGIN}\n## Project Desk\n\n"
            f"This repo coordinates through Project Desk as project `{v['project']}` ({v['name']}). "
            "Before editing, read the Project Desk section of `AGENTS.md` and follow it. "
            "Register with `register_session` and omit `project`; the desk reads it from "
            "`.project-desk.json`. Work only in this project; for work shared with another "
            "project on this desk, use a crossover (see `AGENTS.md`). Read the `lessons` that "
            "`claim_task` returns and `remember` new traps; after a restart, `resume_session` "
            "only your own earlier session.\n\n"
            f"Dashboard: {v['dashboard_url']}\n{END}\n")


def _machine_steps(v):
    return f"""- Claude Code MCP server:

      claude mcp add --transport http --scope user project-desk {v['desk_url']}/mcp

- Codex MCP server, in `~/.codex/config.toml`:

      [mcp_servers.project-desk]
      url = "{v['desk_url']}/mcp"

- Hooks, so agents get inbox and task updates while they work:

      python3 {v['desk_root']}/codex_hooks.py install --agent claude
      python3 {v['desk_root']}/codex_hooks.py install --agent codex

A client needs a restart to see a newly added MCP server."""


def setup_md(project, desk):
    v = _context(project, desk)
    return f"""# Project Desk setup for {v['name']} (`{v['project']}`)

## Once per repo

Run from the repo root, then commit the three files it writes:

    {v['desk_cli']} join {v['dashboard_url']}

## Once per computer

{_machine_steps(v)}
"""


def onboard_md(project, desk):
    v = _context(project, desk)
    return f"""# Join Project Desk: {v['name']} (`{v['project']}`)

You are a coding agent in a repo that coordinates through Project Desk. Follow
these steps in order. Keep any session_key private.

## 1. Connect this repo (once per repo)

From the repo root, run:

    {v['desk_cli']} join {v['dashboard_url']}

It writes `.project-desk.json` and a marked Project Desk section in `AGENTS.md`
and `CLAUDE.md`, changes nothing else, and is safe to run again. It stops if the
repo already declares a different project. Commit those three files.

## 2. Check this computer (once per computer)

The command prints which of these are missing. Tell the human before changing
client configuration or restarting a client.

{_machine_steps(v)}

## 3. Register and start

- `register_session` with your name, agent (`claude` or `codex`), branch and
  absolute worktree. Omit `project`.
- `check_in` with `include=["inbox","counts"]`.
- Follow the rules: {v['dashboard_url']}/rules

## If you cannot run the command

Create these by hand at the repo root.

`.project-desk.json`:

{_indent(declaration(project, desk))}

Add to `AGENTS.md`:

{_indent(agents_block(project, desk))}

Add to `CLAUDE.md`:

{_indent(claude_block(project, desk))}
"""


def kit_file(name, project, desk):
    renderers = {'.project-desk.json': declaration, 'AGENTS.project-desk.md': agents_block,
                 'CLAUDE.project-desk.md': claude_block, 'SETUP.md': setup_md}
    return renderers[name](project, desk)   # KeyError for anything else


def kit_zip(project, desk):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in KIT_FILES:
            archive.writestr(name, kit_file(name, project, desk))
    return buffer.getvalue()
