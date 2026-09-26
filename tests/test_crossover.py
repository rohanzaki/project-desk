"""Crossover: agents in different projects talk, co-claim one piece of work and
both sign it off. Isolation stays the default; only an explicit address or a
crossover opens a door between two projects."""
import json
import sqlite3

import pytest
from starlette.testclient import TestClient

from store import Store, Conflict
from codex_hooks import collect


def declared(tmp_path, slug):
    repo = tmp_path / slug
    repo.mkdir()
    (repo / '.git').mkdir()
    (repo / '.project-desk.json').write_text(json.dumps({'project': slug}))
    return str(repo)


@pytest.fixture
def desk(tmp_path):
    d = Store(tmp_path / 'd.sqlite3', strict=True)
    alpha_repo, beta_repo = declared(tmp_path, 'alpha'), declared(tmp_path, 'beta')
    a = d.register('alpha api agent', 'claude', '', 'main', alpha_repo)
    a2 = d.register('alpha reviewer', 'codex', '', 'main', alpha_repo)
    b = d.register('beta client agent', 'claude', '', 'main', beta_repo)
    return d, a, a2, b


def inbox(d, who):
    return d.check_in(who['session_key'], include=['inbox'])['inbox']


def crossover(d, a, b, alpha_paths=('src/api',), beta_paths=('src/client',)):
    """alpha starts a crossover from its task and invites beta's session; beta joins."""
    ta = d.claim(a['session_key'], 'Tender API', list(alpha_paths), 'Build the endpoint')
    x = d.start_crossover(a['session_key'], ta['id'], [b['session_id']], 'Please build the client side')
    joined = d.join_crossover(b['session_key'], x['id'], 'Build the client', list(beta_paths))
    return ta, x, joined


# ---------- direct messages across projects (existing send_message tool) ----------

def test_direct_message_to_another_projects_session(desk):
    d, a, a2, b = desk
    sent = d.message(a['session_key'], b['session_id'], 'Can your side call POST /tenders?')
    received = inbox(d, b)
    assert [m['id'] for m in received] == [sent['message_id']]
    assert received[0]['from_project'] == 'alpha'
    assert received[0]['from_name'] == 'alpha api agent'
    assert received[0]['project'] == 'beta'
    # it is beta's message: beta acknowledges it, alpha's sessions never see it as theirs
    assert d.acknowledge(b['session_key'], sent['message_id'])['acknowledged_by'] == b['session_id']
    assert inbox(d, a) == [] and inbox(d, a2) == []
    # the reply goes straight back
    reply = d.message(b['session_key'], a['session_id'], 'Yes, send me the schema')
    back = inbox(d, a)
    assert [m['id'] for m in back] == [reply['message_id']] and back[0]['from_project'] == 'beta'


def test_project_addressed_message(desk):
    d, a, a2, b = desk
    d.message(b['session_key'], 'alpha:codex', 'codex reviewers, the client is ready')
    assert [m['body'] for m in inbox(d, a2)] == ['codex reviewers, the client is ready']
    assert inbox(d, a) == []   # a is a claude session
    d.message(b['session_key'], 'alpha:all', 'everyone in alpha')
    assert 'everyone in alpha' in [m['body'] for m in inbox(d, a)]
    # the own project's prefix is just the plain form
    same = d.message(a['session_key'], 'alpha:codex', 'same project')
    assert [m for m in inbox(d, a2) if m['id'] == same['message_id']][0].get('from_project') is None


def test_bad_cross_project_addresses_refused(desk):
    d, a, a2, b = desk
    for recipient in ('nowhere:all', 'beta:everyone', 'Bad Slug:all', 's-000000000000', 'x-000000000000'):
        with pytest.raises(ValueError):
            d.message(a['session_key'], recipient, 'hello?')
    d.update_project('beta', archived=True)
    with pytest.raises(ValueError, match='archived'):
        d.message(a['session_key'], b['session_id'], 'hello?')


def test_imported_sessions_are_not_reachable_across_projects(desk, tmp_path):
    d, a, a2, b = desk
    with d.connection(True) as c:
        c.execute("UPDATE sessions SET imported=1 WHERE id=?", (b['session_id'],))
    with pytest.raises(ValueError):
        d.message(a['session_key'], b['session_id'], 'hello?')


def test_cross_project_message_cannot_carry_senders_task(desk):
    d, a, a2, b = desk
    ta = d.claim(a['session_key'], 'Alpha work', ['src'], 'Edit')
    with pytest.raises(ValueError, match='another project'):
        d.message(a['session_key'], b['session_id'], 'about my task', ta['id'])


def test_messages_table_keeps_its_original_shape(desk):
    """Rollback safety: old code inserts messages positionally (7 columns)."""
    d, a, a2, b = desk
    d.message(a['session_key'], b['session_id'], 'hi')
    with sqlite3.connect(d.path) as c:
        columns = [r[1] for r in c.execute('PRAGMA table_info(messages)')]
    assert columns == ['id', 'project', 'sender', 'recipient', 'body', 'task_id', 'created']


def test_events_land_in_both_projects(desk):
    d, a, a2, b = desk
    sent = d.message(a['session_key'], b['session_id'], 'hi')
    beta_events = d.check_in(b['session_key'], include=['events'])['events']
    alpha_events = d.check_in(a['session_key'], include=['events'])['events']
    assert any(e['kind'] == 'message.sent' and e['data']['message_id'] == sent['message_id']
               and e['data']['from_project'] == 'alpha' for e in beta_events)
    assert any(e['kind'] == 'message.sent_cross' and e['data']['to_project'] == 'beta' for e in alpha_events)
    # the sender's own project never logs a plain message.sent for it (hooks would call it pending)
    assert not any(e['kind'] == 'message.sent' and e['data']['message_id'] == sent['message_id']
                   for e in alpha_events)


def test_board_shows_origin_and_outgoing(desk):
    d, a, a2, b = desk
    sent = d.message(a['session_key'], b['session_id'], 'hi beta')
    beta_board = d.snapshot('beta')
    card = next(m for m in beta_board['messages'] if m['id'] == sent['message_id'])
    assert card['from_project'] == 'alpha' and card['from_name'] == 'alpha api agent'
    alpha_board = d.snapshot('alpha')
    assert all(m['id'] != sent['message_id'] for m in alpha_board['messages'])
    assert [m['id'] for m in alpha_board['outgoing_messages']] == [sent['message_id']]
    assert alpha_board['outgoing_messages'][0]['to_project'] == 'beta'


def test_human_can_reply_across_projects_from_the_dashboard(desk):
    d, a, a2, b = desk
    d.human('beta', 'message', {'recipient': a['session_id'], 'body': 'from the human on the beta board'})
    assert [m['from_project'] for m in inbox(d, a)] == ['beta']


def test_hook_line_names_the_other_project(desk):
    d, a, a2, b = desk
    d.message(a['session_key'], b['session_id'], 'Can your side call POST /tenders?')
    response = d.check_in(b['session_key'])
    state = {'session_id': b['session_id'], 'project': 'beta', 'agent': 'claude'}
    lines, _ = collect(state, response, initial=True)
    line = next(l for l in lines if l.startswith('UNACKNOWLEDGED MESSAGE'))
    assert 'project alpha' in line and 'alpha api agent' in line


# ---------- crossovers ----------

def test_start_crossover_invites_a_session(desk):
    d, a, a2, b = desk
    ta = d.claim(a['session_key'], 'Tender API', ['src/api'], 'Build')
    x = d.start_crossover(a['session_key'], ta['id'], [b['session_id']], 'Build the client side')
    assert x['id'].startswith('x-') and x['title'] == 'Tender API' and x['status'] == 'open'
    by_project = {m['project']: m for m in x['members']}
    assert by_project['alpha']['task_id'] == ta['id'] and by_project['alpha']['role'] == 'origin'
    assert by_project['beta']['task_id'] is None and by_project['beta']['joined'] is None
    invite = inbox(d, b)
    assert len(invite) == 1 and x['id'] in invite[0]['body'] and 'join_crossover' in invite[0]['body']
    assert invite[0]['crossover_id'] == x['id']


def test_start_crossover_by_project_slug_reaches_the_whole_project(desk):
    d, a, a2, b = desk
    tb = d.claim(b['session_key'], 'Client', ['src/client'], 'Build')
    x = d.start_crossover(b['session_key'], tb['id'], ['alpha'])
    assert any(x['id'] in m['body'] for m in inbox(d, a))
    assert any(x['id'] in m['body'] for m in inbox(d, a2))


def test_start_crossover_rules(desk):
    d, a, a2, b = desk
    ta = d.claim(a['session_key'], 'Tender API', ['src/api'], 'Build')
    with pytest.raises(ValueError, match='another project'):
        d.start_crossover(a['session_key'], ta['id'], ['alpha'])
    with pytest.raises(ValueError, match='another project'):
        d.start_crossover(a['session_key'], ta['id'], [a2['session_id']])
    with pytest.raises(ValueError):
        d.start_crossover(a['session_key'], ta['id'], ['nowhere'])
    with pytest.raises(ValueError):
        d.start_crossover(a['session_key'], ta['id'], [])
    with pytest.raises(PermissionError):
        d.start_crossover(a2['session_key'], ta['id'], ['beta'])
    tb = d.claim(b['session_key'], 'Beta work', ['src'], 'Edit')
    with pytest.raises(PermissionError):
        d.start_crossover(a['session_key'], tb['id'], ['beta'])
    done = d.update(a['session_key'], ta['id'], ta['version'], 'DONE', 'x', 'done', 'tests')
    with pytest.raises(Conflict):
        d.start_crossover(a['session_key'], done['id'], ['beta'])


def test_calling_start_again_adds_to_the_same_crossover(desk, tmp_path):
    d, a, a2, b = desk
    gamma = d.register('gamma agent', 'codex', '', 'main', declared(tmp_path, 'gamma'))
    ta = d.claim(a['session_key'], 'Tender API', ['src/api'], 'Build')
    first = d.start_crossover(a['session_key'], ta['id'], ['beta'])
    again = d.start_crossover(a['session_key'], ta['id'], ['gamma', 'beta'])
    assert again['id'] == first['id']
    assert sorted(m['project'] for m in again['members']) == ['alpha', 'beta', 'gamma']
    assert any(first['id'] in m['body'] for m in inbox(d, gamma))


def test_join_creates_a_claimed_task_in_the_joiners_own_project(desk, tmp_path):
    d, a, a2, b = desk
    ta, x, joined = crossover(d, a, b, alpha_paths=('src/shared',), beta_paths=('src/shared',))
    beta_member = next(m for m in joined['members'] if m['project'] == 'beta')
    tb = d.get_task_context(b['session_key'], beta_member['task_id'])['task']
    assert tb['project'] == 'beta' and tb['owner'] == b['session_id'] and tb['resources'] == ['src/shared']
    assert x['id'] in tb['title']
    # claims stay per project: the same path in two repos is not a conflict, but inside beta it is
    assert d.would_conflict(b['session_key'], ['src/shared/x.ts'])['clear'] is True
    other = d.register('beta other', 'codex', '', 'main', str(tmp_path / 'beta'))
    with pytest.raises(Conflict):
        d.claim(other['session_key'], 'Overlap', ['src/shared'], 'x')
    # the origin hears about the join
    assert any('joined' in m['body'] and m['crossover_id'] == x['id'] for m in inbox(d, a))


def test_join_rules(desk, tmp_path):
    d, a, a2, b = desk
    gamma = d.register('gamma agent', 'codex', '', 'main', declared(tmp_path, 'gamma'))
    ta = d.claim(a['session_key'], 'Tender API', ['src/api'], 'Build')
    x = d.start_crossover(a['session_key'], ta['id'], ['beta'])
    with pytest.raises(PermissionError, match='not invited'):
        d.join_crossover(gamma['session_key'], x['id'], 'x', ['src'])
    with pytest.raises(ValueError):
        d.join_crossover(b['session_key'], x['id'], 'x')            # neither resources nor task_id
    with pytest.raises(ValueError):
        d.join_crossover(b['session_key'], 'x-000000000000', 'x', ['src'])
    d.join_crossover(b['session_key'], x['id'], 'x', ['src'])
    with pytest.raises(Conflict, match='already joined'):
        d.join_crossover(b['session_key'], x['id'], 'x', ['docs'])


def test_join_with_an_existing_task(desk):
    d, a, a2, b = desk
    ta = d.claim(a['session_key'], 'Tender API', ['src/api'], 'Build')
    tb = d.claim(b['session_key'], 'Client already underway', ['src/client'], 'Build')
    x = d.start_crossover(a['session_key'], ta['id'], ['beta'])
    joined = d.join_crossover(b['session_key'], x['id'], 'Keep going', task_id=tb['id'])
    assert next(m for m in joined['members'] if m['project'] == 'beta')['task_id'] == tb['id']
    # a task sits in one crossover only
    y_task = d.claim(a['session_key'], 'Another API', ['src/other'], 'Build')
    y = d.start_crossover(a['session_key'], y_task['id'], ['beta'])
    with pytest.raises(Conflict, match='already in crossover'):
        d.join_crossover(b['session_key'], y['id'], 'x', task_id=tb['id'])


def test_crossover_thread_reaches_every_other_member(desk):
    d, a, a2, b = desk
    ta, x, joined = crossover(d, a, b)
    for who in (a, b):
        pending = [m['id'] for m in inbox(d, who)]
        if pending:
            d.acknowledge(who['session_key'], message_ids=pending)
    sent = d.message(b['session_key'], x['id'], 'Client done; please review the payload shape')
    assert sent['status'] == 'sent' and len(sent['message_ids']) == 1
    got = inbox(d, a)
    assert [m['body'] for m in got] == ['Client done; please review the payload shape']
    assert got[0]['crossover_id'] == x['id'] and got[0]['from_project'] == 'beta'
    assert inbox(d, b) == []   # never to yourself
    # any session of a member project may post (a2 is in alpha); both task owners hear it
    fanned = d.message(a2['session_key'], x['id'], 'reviewer note')
    assert len(fanned['message_ids']) == 2
    assert 'reviewer note' in [m['body'] for m in inbox(d, a)]
    assert 'reviewer note' in [m['body'] for m in inbox(d, b)]


def test_outsider_cannot_post_to_a_crossover(desk, tmp_path):
    d, a, a2, b = desk
    gamma = d.register('gamma agent', 'codex', '', 'main', declared(tmp_path, 'gamma'))
    ta, x, joined = crossover(d, a, b)
    with pytest.raises(PermissionError):
        d.message(gamma['session_key'], x['id'], 'let me in')


def test_members_read_each_others_task_context(desk, tmp_path):
    d, a, a2, b = desk
    ta, x, joined = crossover(d, a, b)
    context = d.get_task_context(b['session_key'], ta['id'])
    assert context['task']['id'] == ta['id']
    assert context['crossovers'][0]['id'] == x['id']
    gamma = d.register('gamma agent', 'codex', '', 'main', declared(tmp_path, 'gamma'))
    with pytest.raises(PermissionError, match='another project'):
        d.get_task_context(gamma['session_key'], ta['id'])


def test_both_sides_must_sign_off_before_done(desk):
    d, a, a2, b = desk
    ta, x, joined = crossover(d, a, b)
    tb_id = next(m for m in joined['members'] if m['project'] == 'beta')['task_id']
    ta = d.get_task_context(a['session_key'], ta['id'])['task']
    with pytest.raises(Conflict, match='sign-off from beta'):
        d.update(a['session_key'], ta['id'], ta['version'], 'DONE', 'x', 'API shipped', 'curl ok')
    # only the member task's owner signs for that side
    with pytest.raises(PermissionError):
        d.sign_off_crossover(a2['session_key'], x['id'], 'looks fine')
    signed = d.sign_off_crossover(b['session_key'], x['id'], 'Client calls POST /tenders: 12/12 contract tests pass')
    assert next(m for m in signed['members'] if m['project'] == 'beta')['signed_off'] is True
    assert signed['status'] == 'open'   # alpha has not signed yet
    assert any('SIGN-OFF' in m['body'] for m in inbox(d, a))
    done = d.update(a['session_key'], ta['id'], ta['version'], 'DONE', 'x', 'API shipped', 'curl ok')
    assert done['status'] == 'DONE'
    view = d.check_in(b['session_key'], include=['crossovers'])['crossovers'][0]
    alpha_member = next(m for m in view['members'] if m['project'] == 'alpha')
    assert alpha_member['signed_off'] is True and alpha_member['signoff'] == 'curl ok'   # DONE counts as sign-off
    tb = d.get_task_context(b['session_key'], tb_id)['task']
    closed = d.update(b['session_key'], tb_id, tb['version'], 'DONE', 'x', 'client shipped', 'e2e ok')
    assert closed['status'] == 'DONE'
    assert d.check_in(a['session_key'], include=['crossovers'])['crossovers'] == []   # closed ones drop off
    assert d.snapshot('alpha')['crossovers'][0]['status'] == 'closed'


def test_all_signed_marks_validated(desk):
    d, a, a2, b = desk
    ta, x, joined = crossover(d, a, b)
    d.sign_off_crossover(b['session_key'], x['id'], 'client ok')
    view = d.sign_off_crossover(a['session_key'], x['id'], 'api ok')
    assert view['status'] == 'validated'
    assert any('All sides validated' in m['body'] for m in inbox(d, b))


def test_invited_but_not_joined_does_not_block(desk):
    d, a, a2, b = desk
    ta = d.claim(a['session_key'], 'Tender API', ['src/api'], 'Build')
    d.start_crossover(a['session_key'], ta['id'], ['beta'])
    assert d.update(a['session_key'], ta['id'], ta['version'], 'DONE', 'x', 'shipped', 'ok')['status'] == 'DONE'


def test_human_close_bypasses_and_unblocks(desk):
    d, a, a2, b = desk
    ta, x, joined = crossover(d, a, b)
    tb_id = next(m for m in joined['members'] if m['project'] == 'beta')['task_id']
    tb = d.get_task_context(b['session_key'], tb_id)['task']
    d.human('beta', 'close', {'task_id': tb_id, 'version': tb['version'], 'summary': 'dropped'})
    ta = d.get_task_context(a['session_key'], ta['id'])['task']
    assert d.update(a['session_key'], ta['id'], ta['version'], 'DONE', 'x', 'shipped', 'ok')['status'] == 'DONE'


def test_non_done_updates_are_never_gated(desk):
    d, a, a2, b = desk
    ta, x, joined = crossover(d, a, b)
    ta = d.get_task_context(a['session_key'], ta['id'])['task']
    assert d.update(a['session_key'], ta['id'], ta['version'], 'BLOCKED', 'waiting on beta')['status'] == 'BLOCKED'


def test_update_keeps_the_other_projects_claims(desk, tmp_path):
    d, a, a2, b = desk
    ta, x, joined = crossover(d, a, b, alpha_paths=('src/api',), beta_paths=('src/client',))
    ta = d.get_task_context(a['session_key'], ta['id'])['task']
    d.update(a['session_key'], ta['id'], ta['version'], 'RUNNING', 'wider', resources=['src/api', 'docs'])
    assert d.would_conflict(a2['session_key'], ['docs'])['clear'] is False
    other = d.register('beta other', 'codex', '', 'main', str(tmp_path / 'beta'))
    assert d.would_conflict(other['session_key'], ['src/client'])['clear'] is False


def test_check_in_crossovers_section_and_default_unchanged(desk):
    d, a, a2, b = desk
    ta = d.claim(a['session_key'], 'Tender API', ['src/api'], 'Build')
    x = d.start_crossover(a['session_key'], ta['id'], ['beta'])
    pending = d.check_in(b['session_key'], include=['crossovers'])['crossovers']
    assert [c['id'] for c in pending] == [x['id']]
    assert set(d.check_in(b['session_key'])) == {'session_id', 'cursor', 'events', 'inbox', 'board'}
    with pytest.raises(ValueError):
        d.check_in(b['session_key'], include=['nope'])


def test_list_peers(desk):
    d, a, a2, b = desk
    projects = d.list_peers(a['session_key'])['projects']
    assert {'alpha', 'beta'} <= {p['slug'] for p in projects}
    peers = d.list_peers(a['session_key'], 'beta')
    assert [s['id'] for s in peers['sessions']] == [b['session_id']]
    assert 'worktree' not in peers['sessions'][0] and 'secret_hash' not in peers['sessions'][0]
    with pytest.raises(ValueError):
        d.list_peers(a['session_key'], 'nowhere')


def test_mcp_exposes_crossover_tools(tmp_path, monkeypatch):
    from server import create_app
    monkeypatch.setenv('PROJECT_DESK_STRICT', '1')
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'ROSTER.md')
    store = app.state.store
    a = store.register('alpha', 'claude', '', 'main', declared(tmp_path, 'alpha'))
    b = store.register('beta', 'claude', '', 'main', declared(tmp_path, 'beta'))
    ta = store.claim(a['session_key'], 'Tender API', ['src/api'], 'Build')

    def call(client, name, arguments):
        response = client.post('/mcp', headers={'Accept': 'application/json, text/event-stream'}, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})
        result = response.json()['result']
        assert not result.get('isError'), result
        return result.get('structuredContent') or json.loads(result['content'][0]['text'])

    with TestClient(app) as client:
        listed = client.post('/mcp', headers={'Accept': 'application/json, text/event-stream'},
                             json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}).json()
        names = {t['name'] for t in listed['result']['tools']}
        assert {'start_crossover', 'join_crossover', 'sign_off_crossover', 'list_peers'} <= names
        x = call(client, 'start_crossover', {'session_key': a['session_key'], 'task_id': ta['id'],
                                             'invite': [b['session_id']], 'note': 'client side please'})
        call(client, 'join_crossover', {'session_key': b['session_key'], 'crossover_id': x['id'],
                                        'next_step': 'Build client', 'resources': ['src/client']})
        sent = call(client, 'send_message', {'session_key': b['session_key'], 'recipient': a['session_id'],
                                             'body': 'direct from beta'})
        assert sent['status'] == 'sent'
        peers = call(client, 'list_peers', {'session_key': b['session_key'], 'project': 'alpha'})
        assert peers['sessions'][0]['id'] == a['session_id']
        view = call(client, 'sign_off_crossover', {'session_key': b['session_key'], 'crossover_id': x['id'],
                                                   'validation': 'client ok'})
        assert view['id'] == x['id']


# ---------- the link: tell Claude, or paste a line into the other project's agent ----------

def test_every_view_carries_a_join_link_and_the_invite_mentions_it(desk):
    d, a, a2, b = desk
    ta = d.claim(a['session_key'], 'Tender API', ['src/api'], 'Build')
    x = d.start_crossover(a['session_key'], ta['id'], ['beta'])
    assert x['join_url'].endswith(f"/x/{x['id']}")
    assert x['paste_line'].startswith(f"Join Project Desk crossover {x['id']}") and x['join_url'] in x['paste_line']
    assert any(x['join_url'] in m['body'] for m in inbox(d, b))


def test_join_page(tmp_path, monkeypatch):
    from server import create_app
    monkeypatch.setenv('PROJECT_DESK_STRICT', '1')
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'ROSTER.md')
    store = app.state.store
    a = store.register('alpha api agent', 'claude', '', 'main', declared(tmp_path, 'alpha'))
    store.register('beta client agent', 'claude', '', 'main', declared(tmp_path, 'beta'))
    ta = store.claim(a['session_key'], 'Tender API', ['src/api'], 'Build')
    x = store.start_crossover(a['session_key'], ta['id'], ['beta'])
    with TestClient(app) as client:
        page = client.get(f"/x/{x['id']}")
        assert page.status_code == 200 and page.headers['content-type'].startswith('text/markdown')
        for expected in (x['id'], 'Tender API', 'join_crossover', 'sign_off_crossover', '`beta`: invited', ta['id']):
            assert expected in page.text
        assert client.get('/x/x-000000000000').status_code == 404


def test_human_crosses_a_task_over_from_the_dashboard(tmp_path, monkeypatch):
    from server import create_app
    monkeypatch.setenv('PROJECT_DESK_STRICT', '1')
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'ROSTER.md')
    store = app.state.store
    a = store.register('alpha api agent', 'claude', '', 'main', declared(tmp_path, 'alpha'))
    b = store.register('beta client agent', 'claude', '', 'main', declared(tmp_path, 'beta'))
    ta = store.claim(a['session_key'], 'Tender API', ['src/api'], 'Build')
    headers = {'X-Project-Desk': 'dashboard'}
    with TestClient(app) as client:
        made = client.post('/api/action', headers=headers, json={'project': 'alpha', 'action': 'crossover.start',
                           'data': {'task_id': ta['id'], 'invite': ['beta'], 'note': 'Owner: build the client'}})
        assert made.status_code == 200, made.text
        view = made.json()
        assert view['paste_line'] and view['awaiting_join'] == ['beta']
        invite = store.check_in(b['session_key'], include=['inbox'])['inbox']
        assert invite[0]['from_project'] == 'alpha' and invite[0]['from_name'] == 'Human'
        assert 'Owner: build the client' in invite[0]['body']
        # the task's owner is still the agent; the human only opened the door
        assert store.get_task_context(a['session_key'], ta['id'])['task']['owner'] == a['session_id']
        bad = client.post('/api/action', headers=headers, json={'project': 'alpha', 'action': 'crossover.start',
                          'data': {'task_id': ta['id'], 'invite': ['nowhere']}})
        assert bad.status_code == 400
        wrong = client.post('/api/action', headers=headers, json={'project': 'beta', 'action': 'crossover.start',
                            'data': {'task_id': ta['id'], 'invite': ['alpha']}})
        assert wrong.status_code == 400


def test_moving_a_crossover_task_between_projects_is_refused(desk, tmp_path):
    d, a, a2, b = desk
    ta, x, joined = crossover(d, a, b)
    with d.connection(True) as c:   # make alpha's sessions look idle so move_project would take them
        c.execute("UPDATE sessions SET last_seen='2000-01-01T00:00:00+00:00' WHERE project='alpha'")
    with pytest.raises(Conflict, match='crossover'):
        d.move_project('alpha', 'gamma', str(tmp_path / 'alpha'), apply=True)
    assert d.move_project('alpha', 'gamma', str(tmp_path / 'alpha'))['apply'] is False   # dry run still reports
