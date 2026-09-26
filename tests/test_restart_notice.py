"""A desk restart is announced on every active project's board, before and after."""
import json

import pytest
from starlette.testclient import TestClient

from store import Store


def declared(tmp_path, slug):
    repo = tmp_path / slug
    repo.mkdir(exist_ok=True)
    (repo / '.git').mkdir(exist_ok=True)
    (repo / '.project-desk.json').write_text(json.dumps({'project': slug}))
    return str(repo)


@pytest.fixture
def desk(tmp_path):
    d = Store(tmp_path / 'd.sqlite3', strict=True)
    a = d.register('alpha restarter', 'claude', '', 'main', declared(tmp_path, 'alpha'))
    b = d.register('beta agent', 'claude', '', 'main', declared(tmp_path, 'beta'))
    g = d.register('gamma agent', 'codex', '', 'main', declared(tmp_path, 'gamma'))
    return d, a, b, g


def inbox(d, who):
    return d.check_in(who['session_key'], include=['inbox'])['inbox']


def test_announcing_needs_the_desk_lock(desk):
    d, a, b, g = desk
    with pytest.raises(PermissionError, match='service:project-desk'):
        d.announce_desk_restart(a['session_key'], 60)


def test_the_notice_reaches_every_active_board(desk):
    d, a, b, g = desk
    d.update_project('gamma', archived=True)
    d.claim(a['session_key'], 'Desk release', ['service:project-desk'], 'restart the desk')
    result = d.announce_desk_restart(a['session_key'], 120, 'action items release')
    assert sorted(result['announced_to']) == ['alpha', 'beta'] and 'SendMessage every VS Code peer' in result['next']
    notice = [m for m in inbox(d, b) if m['body'].startswith('PROJECT DESK RESTART in ~2 min')]
    assert notice and notice[0]['from_project'] == 'alpha' and notice[0]['kind'] == 'fyi'
    assert 'action items release' in notice[0]['body']
    assert any(m['body'].startswith('PROJECT DESK RESTART') for m in d.snapshot('alpha')['messages'])
    with pytest.raises(ValueError):
        d.announce_desk_restart(a['session_key'], 99999)


def test_back_notice_once_per_quiet_window(desk):
    d, a, b, g = desk
    sent = d.announce_desk_back('abc1234')
    assert sorted(sent) == ['alpha', 'beta', 'gamma']
    got = [m for m in inbox(d, g) if m['body'].startswith('PROJECT DESK IS BACK')]
    assert got and 'running abc1234' in got[0]['body'] and got[0]['sender'] == 'project-desk'
    assert d.announce_desk_back('abc1234') == {}   # a crash loop does not flood the boards


def test_server_posts_back_on_startup_and_the_dashboard_can_announce(tmp_path):
    from server import create_app
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'ROSTER.md', announce=True)
    store = app.state.store
    store.register('alpha', 'claude', '', 'main', declared(tmp_path, 'alpha'))
    store.register('beta', 'claude', '', 'main', declared(tmp_path, 'beta'))
    with TestClient(app) as client:
        state = client.get('/api/state?project=beta').json()
        assert any(m['body'].startswith('PROJECT DESK IS BACK') for m in state['messages'])
        made = client.post('/api/action', headers={'X-Project-Desk': 'dashboard'}, json={
            'project': 'alpha', 'action': 'desk.announce', 'data': {'seconds': '30', 'reason': 'owner restart'}})
        assert made.status_code == 200 and sorted(made.json()['announced_to']) == ['alpha', 'beta']
        tools = client.post('/mcp', headers={'Accept': 'application/json, text/event-stream'},
                            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}).json()
        assert 'announce_desk_restart' in {t['name'] for t in tools['result']['tools']}


def test_no_back_notice_unless_enabled(tmp_path):
    from server import create_app
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'ROSTER.md', announce=False)
    app.state.store.register('alpha', 'claude', '', 'main', declared(tmp_path, 'alpha'))
    with TestClient(app) as client:
        assert not any(m['body'].startswith('PROJECT DESK IS BACK')
                       for m in client.get('/api/state?project=alpha').json()['messages'])
