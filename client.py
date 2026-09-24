"""Fallback CLI for existing sessions whose MCP catalog has not reloaded,
plus `desk join`, which connects a repo to a Project Desk project."""
import argparse
import asyncio
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from onboarding import BEGIN, END

DEFAULT_DESK = os.environ.get('PROJECT_DESK_URL', 'http://127.0.0.1:7331').rstrip('/')
LINK = re.compile(r'(https?://[^/\s]+)/p/([a-z0-9][a-z0-9-]*)(?:/onboard)?/?')


def upsert_block(path, block):
    """Insert or replace the marked Project Desk block. Returns True when the file changed."""
    path = Path(path)
    original = ''
    if path.exists():
        with open(path, newline='') as stream:
            original = stream.read()
    newline = '\r\n' if '\r\n' in original else '\n'
    body = block.replace('\r\n', '\n').strip('\n')
    if not (body.startswith(BEGIN) and body.endswith(END)):
        raise ValueError('Block must start and end with the Project Desk markers')
    body = body.replace('\n', newline)
    start = original.find(BEGIN)
    if start != -1:
        end = original.find(END, start)
        if end == -1:
            raise ValueError(f'{path.name} has a Project Desk begin marker without an end marker; fix it by hand')
        updated = original[:start] + body + original[end + len(END):]
    elif original.strip():
        updated = original.rstrip('\r\n') + newline + newline + body + newline
    else:
        updated = body + newline
    if updated == original:
        return False
    with open(path, 'w', newline='') as stream:
        stream.write(updated)
    return True


def fetch_text(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode('utf-8')


def machine_status(desk, home=None):
    """One-time setup steps still missing on this computer (never changed here)."""
    home = Path(home or Path.home())
    root = Path(__file__).resolve().parent

    def read(relative):
        try:
            return (home / relative).read_text()
        except OSError:
            return ''
    try:
        claude_servers = json.loads(read('.claude.json') or '{}').get('mcpServers', {})
    except ValueError:
        claude_servers = {}
    missing = []
    if 'project-desk' not in claude_servers:
        missing.append({'what': 'Claude Code MCP server',
                        'run': f'claude mcp add --transport http --scope user project-desk {desk}/mcp'})
    if '[mcp_servers.project-desk]' not in read('.codex/config.toml'):
        missing.append({'what': 'Codex MCP server', 'add_to': str(home / '.codex/config.toml'),
                        'text': f'[mcp_servers.project-desk]\nurl = "{desk}/mcp"\n'})
    if 'codex_hooks.py' not in read('.claude/settings.json'):
        missing.append({'what': 'Claude hooks', 'run': f'python3 {root}/codex_hooks.py install --agent claude'})
    if 'codex_hooks.py' not in read('.codex/hooks.json'):
        missing.append({'what': 'Codex hooks', 'run': f'python3 {root}/codex_hooks.py install --agent codex'})
    return missing


def join(link, repo='.', fetch=fetch_text, home=None):
    match = LINK.fullmatch(str(link).strip())
    if not match:
        raise ValueError('Expected a link like http://127.0.0.1:7331/p/<project>')
    desk, slug = match.group(1), match.group(2)
    base = f'{desk}/p/{slug}'
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise ValueError(f'{repo} is not a folder')
    declaration = fetch(f'{base}/files/.project-desk.json')
    if json.loads(declaration).get('project') != slug:
        raise ValueError('The desk returned a declaration for a different project')
    target = repo / '.project-desk.json'
    if target.exists():
        try:
            existing = json.loads(target.read_text()).get('project')
        except ValueError:
            existing = None
        if existing and existing != slug:
            raise ValueError(f'This repo already declares project {existing}. '
                             'Remove .project-desk.json first if you really mean to move it.')
    changed = []
    if not target.exists() or target.read_text() != declaration:
        target.write_text(declaration)
        changed.append('.project-desk.json')
    if upsert_block(repo / 'AGENTS.md', fetch(f'{base}/files/AGENTS.project-desk.md')):
        changed.append('AGENTS.md')
    if upsert_block(repo / 'CLAUDE.md', fetch(f'{base}/files/CLAUDE.project-desk.md')):
        changed.append('CLAUDE.md')
    return {'repo': str(repo), 'project': slug, 'changed': changed,
            'next': 'Commit the changed files. Then call register_session without a project.',
            'machine_setup_missing': machine_status(desk, home)}


async def call_tools():
    parser = argparse.ArgumentParser()
    parser.add_argument('tool', nargs='?', default='list')
    parser.add_argument('--json-file', help='Arguments JSON file; use - to read stdin')
    parser.add_argument('--url', default=DEFAULT_DESK + '/mcp')
    args = parser.parse_args()
    data = {}
    if args.json_file:
        if args.json_file == '-':
            data = json.load(sys.stdin)
        else:
            with open(args.json_file) as f:
                data = json.load(f)
    async with streamable_http_client(args.url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if args.tool == 'list':
                result = await session.list_tools()
                print(json.dumps([{'name': t.name, 'description': t.description, 'inputSchema': t.inputSchema}
                                  for t in result.tools], indent=2))
            else:
                result = await session.call_tool(args.tool, data)
                if result.isError:
                    print(result.model_dump_json(), file=sys.stderr)
                    raise SystemExit(1)
                print(json.dumps(result.structuredContent or json.loads(result.content[0].text), indent=2))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'join':
        parser = argparse.ArgumentParser(prog='desk join', description='Connect this repo to a Project Desk project.')
        parser.add_argument('link')
        parser.add_argument('--repo', default='.')
        args = parser.parse_args(sys.argv[2:])
        try:
            print(json.dumps(join(args.link, args.repo), indent=2))
        except (ValueError, OSError) as error:
            print(f'desk join: {error}', file=sys.stderr)
            raise SystemExit(1)
        return
    asyncio.run(call_tools())


if __name__ == '__main__':
    main()
