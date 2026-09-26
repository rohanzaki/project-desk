"""What the desk puts into an agent's context, and why it is no bigger than it must be.

Every line a hook injects is paid for again on every later turn of that session,
so these tests pin down what arrives whole (mail for you, the human, questions,
your own tasks), what arrives once as one line (other agents' broadcasts and
status changes), and what does not arrive at all (the board again on every
prompt, a project's whole history on binding, broadcasts from before you started).
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import codex_hooks as hooks
from deskcore import ident
from store import Store

THREAD = '33333333-3333-3333-3333-333333333333'


@pytest.fixture
def desk(tmp_path):
    store = Store(tmp_path / 'desk.sqlite3')
    peer = store.register('Peer', 'claude', 'test', 'peer', '/tmp/peer')
    return store, peer, tmp_path


def call_for(store):
    return lambda tool, args: store.check_in(args['session_key'], args['since'])


def bound(store, tmp_path, name='Me'):
    me = store.register(name, 'codex', 'test', 'me', '/tmp/me')
    path = hooks.bind_credentials(THREAD, me, 'test', tmp_path / 'bindings', call_for(store))
    return me, path


def run(store, tmp_path, event='PreToolUse', **extra):
    return hooks.run_hook({'session_id': THREAD, 'hook_event_name': event, **extra},
                          tmp_path / 'bindings', call_for(store))


def text(result):
    return json.dumps(result)


def state_for(session):
    return {'session_id': session['session_id'], 'session_key': session['session_key'],
            'project': 'test', 'agent': 'codex', 'tasks': {}, 'messages': []}


def old_message(store, sender, recipient, body, hours_ago):
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    mid = ident('m-')
    with store.connection(True) as c:
        c.execute('INSERT INTO messages VALUES(?,?,?,?,?,NULL,?)', (mid, 'test', sender, recipient, body, stamp))
    return mid


# ---- the hook ----------------------------------------------------------------

def test_a_prompt_does_not_restate_the_board_it_already_showed(desk):
    """The bug: every task in `prior` was re-sent on every user prompt."""
    store, peer, _ = desk
    me = store.register('Me', 'codex', 'test', 'me', '/tmp/me')
    for i in range(6):
        store.claim(peer['session_key'], f'Theirs {i}', [f'src/o{i}'], 'n')
    state = state_for(me)
    first = store.check_in(me['session_key'])
    lines, _ = hooks.collect(state, first, initial=True, full=True)
    assert sum(l.startswith('OTHER CLAIM') for l in lines) == 6
    again = store.check_in(me['session_key'], since=state['cursor'])
    lines, _ = hooks.collect(state, again, initial=True, full=False)
    assert not any(l.startswith('OTHER CLAIM') for l in lines)


def test_another_agents_task_is_news_only_when_its_status_or_owner_changes(desk):
    store, peer, _ = desk
    me = store.register('Me', 'codex', 'test', 'me', '/tmp/me')
    task = store.claim(peer['session_key'], 'Theirs', ['src/theirs'], 'step one')
    state = state_for(me)
    hooks.collect(state, store.check_in(me['session_key']), initial=True, full=True)
    with store.connection() as c:
        task = store.task(c, task['id'])
    store.update(peer['session_key'], task['id'], task['version'], 'RUNNING', 'step two, reworded')
    lines, _ = hooks.collect(state, store.check_in(me['session_key'], since=state['cursor']))
    assert lines == [], 'a reworded next step is not news for another agent'
    with store.connection() as c:
        task = store.task(c, task['id'])
    store.update(peer['session_key'], task['id'], task['version'], 'DONE', 'x', 'Shipped the thing', 'tests green')
    lines, _ = hooks.collect(state, store.check_in(me['session_key'], since=state['cursor']))
    assert len(lines) == 1 and lines[0].startswith('OTHER CLAIM') and 'Shipped the thing' in lines[0]
    assert len(lines[0]) < 400


def test_broadcasts_arrive_as_one_line_and_mail_for_you_arrives_whole(desk):
    store, peer, tmp_path = desk
    me, _ = bound(store, tmp_path)
    store.message(peer['session_key'], 'all', 'DEPLOY DONE 10:03: road status\n' + 'detail ' * 200)
    store.message(peer['session_key'], me['session_id'], 'Please review src/api before merge. ' + 'why ' * 60)
    result = text(run(store, tmp_path))
    assert 'UNACKNOWLEDGED BROADCAST' in result and 'DEPLOY DONE 10:03: road status' in result
    assert 'detail detail' not in result
    assert 'UNACKNOWLEDGED MESSAGE' in result and 'why why why' in result
    assert result.index('Please review') < result.index('DEPLOY DONE')


def test_the_humans_broadcasts_and_questions_stay_whole(desk):
    store, peer, tmp_path = desk
    me, _ = bound(store, tmp_path)
    store.human('test', 'message', {'recipient': 'all', 'body': 'From now on\n' + 'rule ' * 50})
    store.ask(peer['session_key'], 'all', 'Which port does the web lookup use?\nContext ' + 'c ' * 40)
    result = text(run(store, tmp_path))
    assert 'rule rule rule' in result and 'Context c c' in result


def test_an_earlier_broadcast_becomes_a_count_on_the_next_prompt(desk):
    store, peer, tmp_path = desk
    me, _ = bound(store, tmp_path)
    b = store.message(peer['session_key'], 'all', 'DEPLOY STARTING 10:00')['message_id']
    d = store.message(peer['session_key'], me['session_id'], 'A direct question')['message_id']
    run(store, tmp_path)
    prompt = text(run(store, tmp_path, 'UserPromptSubmit'))
    assert f'UNACKNOWLEDGED BROADCASTS 1 shown before and still unread ({b})' in prompt
    assert 'DEPLOY STARTING 10:00' not in prompt
    assert d in prompt and 'A direct question' in prompt    # mail for you is repeated until acknowledged


def test_stop_does_not_force_a_turn_for_a_broadcast(desk):
    store, peer, tmp_path = desk
    me, path = bound(store, tmp_path)
    run(store, tmp_path)
    before = path.read_text()
    store.message(peer['session_key'], 'all', 'DEPLOY DONE 11:21: road status restricted')
    assert run(store, tmp_path, 'Stop') == {}
    assert path.read_text() == before, 'nothing is consumed, so the next call still delivers it'
    assert 'road status restricted' in text(run(store, tmp_path))
    store.message(peer['session_key'], me['session_id'], 'Need your answer')
    assert run(store, tmp_path, 'Stop')['decision'] == 'block'


def test_a_new_binding_starts_at_the_present(desk):
    store, peer, tmp_path = desk
    for i in range(250):
        store.note(peer['session_key'], f'old note {i}')
    me, path = bound(store, tmp_path)
    assert hooks.private_read(path)['cursor'] == store.check_in(me['session_key'])['latest_cursor'] > 0
    assert 'old note' not in text(run(store, tmp_path))
    fresh = store.note(peer['session_key'], 'a new note')
    assert fresh['note_id'] in text(run(store, tmp_path))


def test_a_session_start_reshows_the_humans_recent_decisions(desk):
    store, peer, tmp_path = desk
    decision = store.human('test', 'note', {'body': 'Reports stay on the 70b model'})
    me, _ = bound(store, tmp_path)   # starts after the decision's event
    assert decision['note_id'] in text(run(store, tmp_path, 'SessionStart'))


def test_pending_lines_only_when_the_inbox_page_is_full(desk):
    store, peer, _ = desk
    me = store.register('Me', 'codex', 'test', 'me', '/tmp/me')
    mid = store.message(peer['session_key'], 'all', 'hello')['message_id']
    store.acknowledge(me['session_key'], mid)
    lines, _ = hooks.collect(state_for(me), store.check_in(me['session_key']))
    assert not any(l.startswith('PENDING MESSAGE') for l in lines)


# ---- the desk ----------------------------------------------------------------

def test_a_new_session_does_not_inherit_old_broadcasts(desk):
    store, peer, _ = desk
    stale = old_message(store, peer['session_id'], 'all', 'DEPLOY DONE two days ago', 48)
    recent = old_message(store, peer['session_id'], 'all', 'DEPLOY DONE this morning', 6)
    human_recent = old_message(store, 'rohan', 'all', 'Rule from yesterday', 40)
    human_old = old_message(store, 'rohan', 'all', 'Rule from last week', 24 * 5)
    me = store.register('Me', 'codex', 'test', 'me', '/tmp/me')
    old_direct = old_message(store, peer['session_id'], me['session_id'], 'old but addressed to you', 24 * 5)
    unread = {m['id'] for m in store.check_in(me['session_key'], include=['inbox'])['inbox']}
    assert unread == {recent, human_recent, old_direct}
    assert stale not in unread and human_old not in unread
    assert store.check_in(me['session_key'], include=['counts'])['counts']['unread_messages'] == 3


def test_acknowledging_a_backlog_returns_a_count_not_every_id(desk):
    store, peer, _ = desk
    me = store.register('Me', 'codex', 'test', 'me', '/tmp/me')
    for i in range(25):
        store.message(peer['session_key'], 'all', f'DEPLOY DONE {i}')
    out = store.acknowledge_inbox(me['session_key'])
    assert out['acknowledged'] == 25 and len(out['message_ids']) == 20 and out['more_ids'] == 5
    assert store.check_in(me['session_key'], include=['counts'])['counts']['unread_messages'] == 0


def test_the_inbox_digest_is_capped_and_says_how_many_more(desk):
    store, peer, _ = desk
    me = store.register('Me', 'codex', 'test', 'me', '/tmp/me')
    for i in range(45):
        store.message(peer['session_key'], 'all', f'FYI {i}')
    direct = store.message(peer['session_key'], me['session_id'], 'for you')['message_id']
    out = store.check_in(me['session_key'], include=['inbox_digest'])
    assert len(out['inbox_digest']) == 40 and out['inbox_digest_more'] == 6
    assert out['inbox_digest'][0]['id'] == direct


def test_a_bare_check_in_over_mcp_is_compact(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    from server import create_app
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'ROSTER.md')
    a = app.state.store.register('alpha', 'claude', '', 'main', '/tmp/alpha')

    def call(client, arguments):
        response = client.post('/mcp', headers={'Accept': 'application/json, text/event-stream'}, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'check_in', 'arguments': arguments}})
        result = response.json()['result']
        assert not result.get('isError'), result
        return result.get('structuredContent') or json.loads(result['content'][0]['text'])

    with TestClient(app) as client:
        bare = call(client, {'session_key': a['session_key']})
        assert set(bare) == {'session_id', 'cursor', 'inbox_digest', 'counts', 'my_tasks', 'note'}
        named = call(client, {'session_key': a['session_key'], 'include': ['events', 'inbox', 'board']})
        assert {'board', 'events', 'inbox', 'latest_cursor'} <= set(named)
