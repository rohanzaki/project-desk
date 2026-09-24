import importlib
import json

import pytest

import client
import onboarding

P = {'slug': 'xyz-app', 'name': 'XYZ App', 'rules_path': ''}
DESK = 'http://127.0.0.1:7331'
LINK = f'{DESK}/p/xyz-app'


def fake_fetch(url):
    name = url.rsplit('/files/', 1)[1]
    return onboarding.kit_file(name, P, DESK)


BLOCK = f'{onboarding.BEGIN}\nhello\n{onboarding.END}\n'


def test_upsert_into_missing_file(tmp_path):
    target = tmp_path / 'AGENTS.md'
    assert client.upsert_block(target, BLOCK) is True
    assert target.read_text() == BLOCK


def test_upsert_appends_after_existing_content_and_is_idempotent(tmp_path):
    target = tmp_path / 'AGENTS.md'
    target.write_text('# Our rules\nkeep me')              # no trailing newline
    assert client.upsert_block(target, BLOCK) is True
    assert target.read_text() == '# Our rules\nkeep me\n\n' + BLOCK
    assert client.upsert_block(target, BLOCK) is False


def test_upsert_replaces_only_the_block(tmp_path):
    target = tmp_path / 'AGENTS.md'
    target.write_text(f'top\n\n{BLOCK}\nbottom\n')
    newer = BLOCK.replace('hello', 'hello v2')
    assert client.upsert_block(target, newer) is True
    assert target.read_text() == f'top\n\n{newer}\nbottom\n'


def test_upsert_keeps_crlf(tmp_path):
    target = tmp_path / 'AGENTS.md'
    target.write_bytes(b'line one\r\nline two\r\n')
    client.upsert_block(target, BLOCK)
    data = target.read_bytes()
    assert b'\r\n' in data and b'\n' not in data.replace(b'\r\n', b'')


def test_upsert_refuses_a_broken_block(tmp_path):
    target = tmp_path / 'AGENTS.md'
    target.write_text(f'{onboarding.BEGIN}\nno end marker\n')
    with pytest.raises(ValueError, match='without an end marker'):
        client.upsert_block(target, BLOCK)


def test_join_writes_three_files_then_nothing(tmp_path):
    first = client.join(LINK, tmp_path, fake_fetch, home=tmp_path / 'home')
    assert first['project'] == 'xyz-app'
    assert sorted(first['changed']) == ['.project-desk.json', 'AGENTS.md', 'CLAUDE.md']
    assert json.loads((tmp_path / '.project-desk.json').read_text())['project'] == 'xyz-app'
    assert client.join(LINK + '/onboard', tmp_path, fake_fetch, home=tmp_path / 'home')['changed'] == []


def test_join_refuses_a_repo_declaring_another_project(tmp_path):
    (tmp_path / '.project-desk.json').write_text(json.dumps({'project': 'media-intelligence'}))
    with pytest.raises(ValueError, match='already declares project media-intelligence'):
        client.join(LINK, tmp_path, fake_fetch, home=tmp_path)


def test_join_rejects_a_bad_link(tmp_path):
    with pytest.raises(ValueError, match='Expected a link'):
        client.join('http://127.0.0.1:7331/something-else', tmp_path, fake_fetch, home=tmp_path)


def test_machine_status(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    assert len(client.machine_status(DESK, home)) == 4
    (home / '.claude.json').write_text(json.dumps({'mcpServers': {'project-desk': {'url': DESK + '/mcp'}}}))
    (home / '.codex').mkdir()
    (home / '.codex' / 'config.toml').write_text('[mcp_servers.project-desk]\nurl = "x"\n')
    (home / '.codex' / 'hooks.json').write_text('{"cmd": "codex_hooks.py"}')
    (home / '.claude').mkdir()
    (home / '.claude' / 'settings.json').write_text('{"cmd": "codex_hooks.py"}')
    assert client.machine_status(DESK, home) == []


def test_desk_url_comes_from_environment(monkeypatch):
    monkeypatch.setenv('PROJECT_DESK_URL', 'http://127.0.0.1:7399/')
    assert importlib.reload(client).DEFAULT_DESK == 'http://127.0.0.1:7399'
    monkeypatch.delenv('PROJECT_DESK_URL')
    importlib.reload(client)
