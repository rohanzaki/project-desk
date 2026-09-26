"""Dashboard v2 server surface: desk_board.py (BoardMixin).

Covers GET /api/board, /api/task, /api/attention, /api/digest, /api/search,
/api/events, /api/lanes; ETag/304; refused-claim recording (the agent's
Conflict text stays byte-identical); and the new human actions (ack.inbox,
answer, refusal.dismiss/queue/handoff, snooze.set/clear).
"""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from desk_board import _ATTENTION_ORDER
from server import create_app
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
    repo = declared(tmp_path, 'alpha')
    a = d.register('alpha builder', 'claude', '', 'main', repo)
    b = d.register('alpha reviewer', 'codex', '', 'main', repo)
    return d, a, b


def fresh(d, t):
    with d.connection() as c:
        return d.task(c, t['id'])


def backdate_session(d, session_id, hours):
    with d.connection(True) as c:
        c.execute('UPDATE sessions SET last_seen=? WHERE id=?',
                  ((datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(), session_id))


def backdate_task(d, task_id, days):
    with d.connection(True) as c:
        c.execute('UPDATE tasks SET updated=? WHERE id=?',
                  ((datetime.now(timezone.utc) - timedelta(days=days)).isoformat(), task_id))


# ---- GET /api/board ----------------------------------------------------------

def test_board_shape_and_done_days_filter(desk):
    d, a, b = desk
    t1 = d.claim(a['session_key'], 'Open task', ['src/open'], 'x')
    t2 = fresh(d, d.claim(a['session_key'], 'Old done task', ['src/olddone'], 'x'))
    d.update(a['session_key'], t2['id'], t2['version'], 'DONE', 'x', 'done', 'ok')
    backdate_task(d, t2['id'], 30)
    board = d.board('alpha', done_days=7)
    ids = {t['id'] for t in board['tasks']}
    assert t1['id'] in ids and t2['id'] not in ids
    assert board['tasks_done_total'] == 1
    assert board['tasks_done_shown'] == 0
    board_all = d.board('alpha', done_days=3650)
    assert t2['id'] in {t['id'] for t in board_all['tasks']}
    assert board_all['tasks_done_shown'] == 1
    for key in ('project', 'generated_at', 'version', 'sessions', 'tasks', 'tasks_done_total',
               'tasks_done_shown', 'evidence', 'messages', 'outgoing_messages', 'notes', 'lessons',
               'approvals', 'open_questions', 'queues', 'prod', 'shared_locks', 'crossovers',
               'stale_claims', 'action_items', 'handoff_briefs', 'refusals', 'snoozes'):
        assert key in board, key
    assert board['project'] == 'alpha'


def test_board_sessions_seen_or_owning_open_task(desk):
    d, a, b = desk
    backdate_session(d, b['session_id'], 10 * 24)   # 10 days: outside the 7-day window
    board = d.board('alpha')
    ids = {s['id'] for s in board['sessions']}
    assert a['session_id'] in ids and b['session_id'] not in ids
    d.claim(b['session_key'], 'B task', ['src/b'], 'x')   # now owns an open task -> included
    board2 = d.board('alpha')
    ids2 = {s['id'] for s in board2['sessions']}
    assert b['session_id'] in ids2
    # claiming refreshed presence (auth() bumps last_seen), so re-backdate it to prove
    # the inclusion really comes from owning an open task, not from recency
    backdate_session(d, b['session_id'], 10 * 24)
    board3 = d.board('alpha')
    stale_entry = next(s for s in board3['sessions'] if s['id'] == b['session_id'])
    assert stale_entry['stale'] is True


def test_board_handoff_briefs_only_offered(desk):
    d, a, b = desk
    t = fresh(d, d.claim(a['session_key'], 'T', ['src/t'], 'x'))
    d.prepare_handoff(a['session_key'], t['id'], t['version'], b['session_id'], 'progress', 'remaining',
                      'validation', 'risks', 'commit', 'main', '/tmp/x', [])
    board = d.board('alpha')
    assert len(board['handoff_briefs']) == 1 and board['handoff_briefs'][0]['status'] == 'offered'
    t = fresh(d, t)
    d.handoff(b['session_key'], t['id'], t['version'], '', accept=True)
    assert d.board('alpha')['handoff_briefs'] == []


def test_board_snoozes_shape(desk):
    d, a, b = desk
    until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    d.human('alpha', 'snooze.set', {'key': 'k1', 'until': until})
    assert d.board('alpha')['snoozes'] == {'k1': until}


# ---- ETag / 304 and bad params over HTTP -------------------------------------

def test_board_etag_304(tmp_path):
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'ROSTER.md')
    app.state.store.register('alpha', 'claude', '', 'main', declared(tmp_path, 'alpha'))
    with TestClient(app) as client:
        r1 = client.get('/api/board', params={'project': 'alpha'})
        assert r1.status_code == 200
        etag = r1.headers['etag']
        assert etag == r1.json()['version']
        assert etag.startswith('"') and etag.endswith('"')
        r2 = client.get('/api/board', params={'project': 'alpha'}, headers={'If-None-Match': etag})
        assert r2.status_code == 304 and r2.text == ''
        r3 = client.get('/api/board', params={'project': 'alpha'}, headers={'If-None-Match': '"0-stale"'})
        assert r3.status_code == 200


def test_new_routes_reject_bad_params(tmp_path):
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'ROSTER.md')
    with TestClient(app) as client:
        assert client.get('/api/board', params={'done_days': 'x'}).status_code == 400
        assert client.get('/api/board', params={'done_days': '-1'}).status_code == 400
        assert client.get('/api/task').status_code == 400
        assert client.get('/api/task', params={'id': 't-doesnotexist'}).status_code == 400
        assert client.get('/api/attention', params={'projects': 'nope'}).status_code == 400
        assert client.get('/api/digest', params={'project': 'default'}).status_code == 400
        assert client.get('/api/digest', params={'project': 'default', 'since': 'not-a-date'}).status_code == 400
        assert client.get('/api/search', params={'q': 'a'}).status_code == 400
        assert client.get('/api/events', params={'before': 'x'}).status_code == 400
        assert client.get('/api/lanes', params={'hours': '0'}).status_code == 400
        assert client.get('/api/board', params={'project': 'alpha'}).status_code == 200


def test_v2_route_404_when_no_page(tmp_path, monkeypatch):
    import server
    (tmp_path / 'static').mkdir()   # StaticFiles still needs a real directory to mount
    monkeypatch.setattr(server, 'ROOT', tmp_path)   # no static/v2/index.html under a bare tmp_path
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'ROSTER.md')
    with TestClient(app) as client:
        assert client.get('/v2').status_code == 404


# ---- GET /api/task -------------------------------------------------------------

def test_task_endpoint_shape(desk):
    d, a, b = desk
    t = fresh(d, d.claim(a['session_key'], 'T', ['src/t'], 'x'))
    d.add_action_items(a['session_key'], ['do x'], t['id'])
    d.message(a['session_key'], b['session_id'], 'hello', t['id'])
    d.log_progress(a['session_key'], t['id'], 'progress note')
    detail = d.task_detail('alpha', t['id'])
    for key in ('task', 'journal', 'messages', 'action_items', 'evidence', 'lessons', 'handoff_briefs',
               'crossover'):
        assert key in detail
    assert detail['task']['id'] == t['id']
    assert detail['journal'][-1]['entry'] == 'progress note'
    assert len(detail['messages']) == 1 and detail['messages'][0]['acknowledgments'] == []
    assert detail['action_items'][0]['body'] == 'do x'
    assert detail['crossover'] is None


def test_task_endpoint_refuses_wrong_project(desk):
    d, a, b = desk
    t = d.claim(a['session_key'], 'T', ['src/t'], 'x')
    with pytest.raises(ValueError):
        d.task_detail('beta', t['id'])


# ---- GET /api/attention ---------------------------------------------------------

def test_attention_approval_and_question(desk):
    d, a, b = desk
    t = fresh(d, d.claim(a['session_key'], 'T', ['src/t'], 'x'))
    d.request_approval(a['session_key'], 'Ship?', ['yes', 'no'], 'context', t['id'])
    d.ask(a['session_key'], 'human', 'Is this ok?', t['id'])
    att = d.attention('alpha')
    kinds = {i['kind'] for i in att['items']}
    assert kinds == {'approval', 'question'}
    approval_item = next(i for i in att['items'] if i['kind'] == 'approval')
    assert approval_item['task_id'] == t['id'] and approval_item['who']['id'] == a['session_id']
    assert approval_item['approval_id'] and approval_item['options'] == ['yes', 'no']
    question_item = next(i for i in att['items'] if i['kind'] == 'question')
    assert question_item['body'] == 'Is this ok?' and question_item['who']['id'] == a['session_id']
    assert att['counts']['alpha'] == 2 and att['total'] == 2


def test_attention_stale(desk):
    d, a, b = desk
    t = fresh(d, d.claim(a['session_key'], 'T', ['src/t'], 'x'))
    backdate_session(d, a['session_id'], 7)
    att = d.attention('alpha')
    stale_items = [i for i in att['items'] if i['kind'] == 'stale']
    assert len(stale_items) == 1 and stale_items[0]['task_id'] == t['id']
    assert stale_items[0]['who']['kind'] == 'claude'
    assert stale_items[0]['who']['id'] == a['session_id']


def test_attention_refused(desk):
    d, a, b = desk
    held = d.claim(a['session_key'], 'Held', ['src/shared'], 'x')
    with pytest.raises(Exception):
        d.claim(b['session_key'], 'Wants it too', ['src/shared'], 'y')
    att = d.attention('alpha')
    refused = [i for i in att['items'] if i['kind'] == 'refused']
    assert len(refused) == 1
    assert refused[0]['who']['id'] == b['session_id']
    assert refused[0]['holder_session'] == a['session_id']
    assert refused[0]['holder_task_id'] == held['id']
    assert refused[0]['resources'] == ['src/shared']


def test_attention_action_grouped_by_task(desk):
    d, a, b = desk
    t = fresh(d, d.claim(a['session_key'], 'T', ['src/t'], 'x'))
    d.add_action_items(a['session_key'], ['Item one', 'Item two'], t['id'])
    att = d.attention('alpha')
    action_items = [i for i in att['items'] if i['kind'] == 'action']
    assert len(action_items) == 1
    assert sorted(action_items[0]['bodies']) == ['Item one', 'Item two']
    assert sorted(action_items[0]['item_ids']) == sorted(action_items[0]['item_ids'])
    assert action_items[0]['task_id'] == t['id']
    assert action_items[0]['key'] == f"action:alpha:{t['id']}"


def test_attention_blocked_regex_and_exclusions(desk):
    d, a, b = desk
    t1 = fresh(d, d.claim(a['session_key'], 'Needs approval', ['src/needs'], 'x'))
    d.update(a['session_key'], t1['id'], t1['version'], 'BLOCKED', 'Waiting on rohan to confirm the approach')
    t2 = fresh(d, d.claim(a['session_key'], 'Needs infra', ['src/needs2'], 'x'))
    d.update(a['session_key'], t2['id'], t2['version'], 'BLOCKED', 'Waiting on the API to come back up')
    att = d.attention('alpha')
    blocked = [i for i in att['items'] if i['kind'] == 'blocked']
    assert [i['task_id'] for i in blocked] == [t1['id']]


def test_attention_blocked_excluded_when_covered_by_approval(desk):
    d, a, b = desk
    t = fresh(d, d.claim(a['session_key'], 'T', ['src/t'], 'x'))
    d.update(a['session_key'], t['id'], t['version'], 'BLOCKED', 'need your decision')
    d.request_approval(a['session_key'], 'Decide', ['a', 'b'], 'context', t['id'])
    att = d.attention('alpha')
    assert not any(i['kind'] == 'blocked' for i in att['items'])
    assert any(i['kind'] == 'approval' for i in att['items'])


def test_attention_snooze_excludes_and_include_snoozed_shows_until(desk):
    d, a, b = desk
    t = fresh(d, d.claim(a['session_key'], 'T', ['src/t'], 'x'))
    d.request_approval(a['session_key'], 'Ship?', ['yes', 'no'], 'ctx', t['id'])
    approval_id = d.board('alpha')['approvals'][0]['id']
    key = f'approval:{approval_id}'
    until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    d.human('alpha', 'snooze.set', {'key': key, 'until': until})
    att = d.attention('alpha')
    assert not any(i['key'] == key for i in att['items'])
    att2 = d.attention('alpha', include_snoozed=True)
    item = next(i for i in att2['items'] if i['key'] == key)
    assert item['snoozed_until'] == until
    # an expired snooze does not hide it
    d.human('alpha', 'snooze.set', {'key': key, 'until': (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()})
    att3 = d.attention('alpha')
    assert any(i['key'] == key for i in att3['items'])


def test_attention_overall_sort_order(desk):
    d, a, b = desk
    t = fresh(d, d.claim(a['session_key'], 'T', ['src/t'], 'x'))
    d.request_approval(a['session_key'], 'Ship?', ['y', 'n'], 'ctx', t['id'])
    d.ask(a['session_key'], 'human', 'ok?', t['id'])
    t2 = fresh(d, d.claim(a['session_key'], 'T2', ['src/t2'], 'x'))
    d.update(a['session_key'], t2['id'], t2['version'], 'BLOCKED', 'need your ok to proceed')
    d.add_action_items(a['session_key'], ['todo'], t['id'])
    backdate_session(d, a['session_id'], 7)   # also makes t and t2 stale-owner claims
    held = d.claim(b['session_key'], 'Held', ['src/shared'], 'x')
    att = d.attention('alpha')
    kinds = [i['kind'] for i in att['items']]
    order = ['approval', 'question', 'blocked', 'stale', 'refused', 'action']
    seen = []
    for k in kinds:
        if k not in seen:
            seen.append(k)
    assert seen == [k for k in order if k in kinds]


def test_attention_unknown_project(desk):
    d, a, b = desk
    with pytest.raises(ValueError):
        d.attention('nope')
    with pytest.raises(ValueError):
        d.attention('alpha,nope')


# ---- refused claims: recording + the agent's Conflict text is unchanged --------

def test_refusal_recorded_and_conflict_text_unchanged(desk):
    d, a, b = desk
    held = d.claim(a['session_key'], 'Held', ['src/shared'], 'x')
    with pytest.raises(Exception) as exc:
        d.claim(b['session_key'], 'Wants it', ['src/shared'], 'y')
    message = str(exc.value)
    assert message == f"Resource overlaps src/shared held by {held['id']}. Request a handoff; do not edit."
    board = d.board('alpha')
    assert len(board['refusals']) == 1
    r = board['refusals'][0]
    assert r['session_id'] == b['session_id']
    assert r['holder_task_id'] == held['id']
    assert r['holder_session'] == a['session_id']
    assert r['holder_project'] == 'alpha'
    assert r['resources'] == ['src/shared']
    assert r['status'] == 'open'
    assert r['title'] == 'Wants it'
    with d.connection() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE title='Wants it'").fetchone()   # rolled back cleanly
    events = d.events_page('alpha', limit=50)['events']
    assert any(e['kind'] == 'claim.refused' for e in events)


def test_refusal_not_recorded_for_non_overlap_conflicts(desk):
    d, a, b = desk
    t = d.human('alpha', 'create', {'title': 'Queued', 'resources': ['src/q'], 'next_step': 'x'})
    d.claim(a['session_key'], 'ignored', t['resources'], 'y', task_id=t['id'])
    with pytest.raises(Exception):
        d.claim(b['session_key'], 'ignored', t['resources'], 'y', task_id=t['id'])
    assert d.board('alpha')['refusals'] == []


def test_refusal_claiming_still_works_normally(desk):
    d, a, b = desk
    t = d.claim(a['session_key'], 'Free to claim', ['src/free'], 'x')
    assert t['status'] == 'RUNNING'
    assert d.board('alpha')['refusals'] == []


# ---- new human actions -----------------------------------------------------------

def test_action_ack_inbox(desk):
    d, a, b = desk
    d.message(a['session_key'], 'all', 'FYI broadcast 1')
    d.message(a['session_key'], 'all', 'FYI broadcast 2')
    d.message(a['session_key'], 'human', 'direct to human')
    result = d.human('alpha', 'ack.inbox', {})
    assert result == {'acknowledged': 2}
    with d.connection() as c:
        acked = c.execute("SELECT COUNT(*) FROM receipts WHERE session_id='rohan'").fetchone()[0]
    assert acked == 2
    assert d.human('alpha', 'ack.inbox', {})['acknowledged'] == 0   # nothing left to ack


def test_action_ack_inbox_kind_filter(desk):
    d, a, b = desk
    d.message(a['session_key'], 'all', 'DEPLOY DONE something', kind='deploy')
    d.message(a['session_key'], 'all', 'FYI something', kind='fyi')
    result = d.human('alpha', 'ack.inbox', {'kinds': ['deploy']})
    assert result['acknowledged'] == 1
    result2 = d.human('alpha', 'ack.inbox', {})
    assert result2['acknowledged'] == 1
    with pytest.raises(ValueError):
        d.human('alpha', 'ack.inbox', {'kinds': ['nonsense']})


def test_action_answer_marks_question_answered_and_optional_decision(desk):
    d, a, b = desk
    t = fresh(d, d.claim(a['session_key'], 'T', ['src/t'], 'x'))
    q = d.ask(a['session_key'], 'human', 'Should I proceed?', t['id'])
    result = d.human('alpha', 'answer', {'message_id': q['question_id'], 'body': 'Yes, proceed',
                                         'also_decision': True})
    assert result['status'] == 'sent'
    with d.connection() as c:
        question = c.execute('SELECT status,answered_by FROM questions WHERE message_id=?',
                             (q['question_id'],)).fetchone()
    assert question['status'] == 'answered' and question['answered_by'] == 'rohan'
    board = d.board('alpha')
    assert any(n['kind'] == 'decision' and n['body'] == 'Yes, proceed' for n in board['notes'])
    inbox = d.check_in(a['session_key'], include=['inbox'])['inbox']
    assert any(m['body'] == 'Yes, proceed' for m in inbox)


def test_action_answer_without_decision_skips_note(desk):
    d, a, b = desk
    t = fresh(d, d.claim(a['session_key'], 'T', ['src/t'], 'x'))
    q = d.ask(a['session_key'], 'human', 'Ok?', t['id'])
    d.human('alpha', 'answer', {'message_id': q['question_id'], 'body': 'Sure'})
    assert d.board('alpha')['notes'] == []


def test_action_refusal_dismiss(desk):
    d, a, b = desk
    d.claim(a['session_key'], 'Held', ['src/shared'], 'x')
    with pytest.raises(Exception):
        d.claim(b['session_key'], 'Wants it', ['src/shared'], 'y')
    refusal_id = d.board('alpha')['refusals'][0]['id']
    result = d.human('alpha', 'refusal.dismiss', {'refusal_id': refusal_id})
    assert result == {'refusal_id': refusal_id, 'status': 'dismissed'}
    assert d.board('alpha')['refusals'] == []


def test_action_refusal_queue(desk):
    d, a, b = desk
    d.claim(a['session_key'], 'Held', ['src/shared'], 'x')
    with pytest.raises(Exception):
        d.claim(b['session_key'], 'Wants it', ['src/shared'], 'y')
    refusal_id = d.board('alpha')['refusals'][0]['id']
    result = d.human('alpha', 'refusal.queue', {'refusal_id': refusal_id, 'note': 'please queue'})
    assert result['status'] == 'queued'
    wc = d.would_conflict(b['session_key'], ['src/shared'])
    assert any(q['session_id'] == b['session_id'] for q in wc['queues']['src/shared'])


def test_action_refusal_handoff(desk):
    d, a, b = desk
    d.claim(a['session_key'], 'Held', ['src/shared'], 'x')
    with pytest.raises(Exception):
        d.claim(b['session_key'], 'Wants it', ['src/shared'], 'y')
    refusal_id = d.board('alpha')['refusals'][0]['id']
    result = d.human('alpha', 'refusal.handoff', {'refusal_id': refusal_id, 'note': 'please hand off'})
    assert result['status'] == 'handoff_asked'
    inbox = d.check_in(a['session_key'], include=['inbox'])['inbox']
    assert any('HANDOFF REQUEST' in m['body'] for m in inbox)


def test_action_refusal_unknown_and_already_resolved(desk):
    d, a, b = desk
    with pytest.raises(ValueError):
        d.human('alpha', 'refusal.dismiss', {'refusal_id': 'r-nope'})
    d.claim(a['session_key'], 'Held', ['src/shared'], 'x')
    with pytest.raises(Exception):
        d.claim(b['session_key'], 'Wants it', ['src/shared'], 'y')
    refusal_id = d.board('alpha')['refusals'][0]['id']
    d.human('alpha', 'refusal.dismiss', {'refusal_id': refusal_id})
    with pytest.raises(Exception):
        d.human('alpha', 'refusal.dismiss', {'refusal_id': refusal_id})


def test_action_snooze_set_and_clear(desk):
    d, a, b = desk
    until = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    result = d.human('alpha', 'snooze.set', {'key': 'needs-you:foo', 'until': until})
    assert result == {'key': 'needs-you:foo', 'until': until}
    assert d.board('alpha')['snoozes'] == {'needs-you:foo': until}
    d.human('alpha', 'snooze.clear', {'key': 'needs-you:foo'})
    assert d.board('alpha')['snoozes'] == {}


def test_action_snooze_bad_until(desk):
    d, a, b = desk
    with pytest.raises(ValueError):
        d.human('alpha', 'snooze.set', {'key': 'k', 'until': 'not-a-date'})


def test_new_actions_over_http(tmp_path):
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'ROSTER.md')
    store = app.state.store
    a = store.register('alpha', 'claude', '', 'main', declared(tmp_path, 'alpha'))
    headers = {'X-Project-Desk': 'dashboard'}
    with TestClient(app) as client:
        store.message(a['session_key'], 'all', 'broadcast')
        r = client.post('/api/action', headers=headers,
                        json={'project': 'alpha', 'action': 'ack.inbox', 'data': {}})
        assert r.status_code == 200 and r.json()['acknowledged'] == 1


# ---- GET /api/digest --------------------------------------------------------------

def test_digest_windows(desk):
    d, a, b = desk
    since = datetime.now(timezone.utc).isoformat()
    t = fresh(d, d.claim(a['session_key'], 'T', ['src/t'], 'x'))
    d.update(a['session_key'], t['id'], t['version'], 'DONE', 'x', 'shipped', 'tests green')
    d.record_deploy(a['session_key'], 'web', 'abc', 'deployed', t['id'])
    d.message(a['session_key'], 'human', 'hi human')
    digest = d.digest('alpha', since)
    assert digest['done'][0]['task_id'] == t['id'] and digest['done'][0]['owner_name'] == 'alpha builder'
    assert digest['deploys'][0]['service'] == 'web' and digest['deploys'][0]['commit_ref'] == 'abc'
    assert digest['claimed'][0]['task_id'] == t['id']
    assert digest['messages_to_you'] == 1
    assert digest['since'] == since and digest['until']
    future = d.digest('alpha', (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    assert future['done'] == [] and future['claimed'] == [] and future['messages_to_you'] == 0


def test_digest_questions_approvals_decisions(desk):
    d, a, b = desk
    since = datetime.now(timezone.utc).isoformat()
    t = fresh(d, d.claim(a['session_key'], 'T', ['src/t'], 'x'))
    d.ask(a['session_key'], 'human', 'ok?', t['id'])
    d.request_approval(a['session_key'], 'Ship?', ['y', 'n'], 'ctx', t['id'])
    d.human('alpha', 'note', {'body': 'A decision was made'})
    digest = d.digest('alpha', since)
    assert digest['questions'][0]['recipient'] == 'rohan'
    assert digest['approvals'][0]['title'] == 'Ship?'
    assert any(dd['body'] == 'A decision was made' for dd in digest['decisions'])


# ---- GET /api/search ---------------------------------------------------------------

def test_search_kinds_and_snippet(desk):
    d, a, b = desk
    t = fresh(d, d.claim(a['session_key'], 'Rare unicorn task', ['src/unicorn'], 'x'))
    d.message(a['session_key'], 'human', 'A message mentioning a unicorn parade')
    d.note(a['session_key'], 'A note about the unicorn plan')
    d.remember(a['session_key'], 'Lesson: unicorns need care', ['src/unicorn'])
    result = d.search('alpha', 'unicorn')
    assert any(x['id'] == t['id'] for x in result['tasks'])
    assert any('unicorn' in m['snippet'].lower() for m in result['messages'])
    assert any('unicorn' in n['snippet'].lower() for n in result['notes'])
    assert any('unicorn' in l['snippet'].lower() for l in result['lessons'])
    assert all(len(result[k]) <= 10 for k in ('tasks', 'messages', 'notes', 'lessons'))


def test_search_too_short(desk):
    d, a, b = desk
    with pytest.raises(ValueError):
        d.search('alpha', 'a')
    with pytest.raises(ValueError):
        d.search('alpha', '')


def test_search_case_insensitive(desk):
    d, a, b = desk
    d.claim(a['session_key'], 'Ship the Rocket', ['src/rocket'], 'x')
    result = d.search('alpha', 'ROCKET')
    assert any('Rocket' in t['title'] for t in result['tasks'])


# ---- GET /api/events -----------------------------------------------------------------

def test_events_page_text_and_paging(desk):
    d, a, b = desk
    t = fresh(d, d.claim(a['session_key'], 'T', ['src/t'], 'x'))
    d.record_deploy(a['session_key'], 'web', 'abc123', 'ship', t['id'])
    d.message(a['session_key'], 'human', 'hey')
    page = d.events_page('alpha', limit=2)
    assert len(page['events']) == 2
    assert page['events'][0]['actor_name'] == 'alpha builder'
    all_events = d.events_page('alpha', limit=50)['events']
    deploy_event = next(e for e in all_events if e['kind'] == 'deploy.recorded')
    assert deploy_event['text'] == 'deployed web at abc123'
    claim_event = next(e for e in all_events if e['kind'] == 'task.claimed')
    assert claim_event['text'].startswith(f"claimed {t['id']} T (")
    ack_message_id = d.message(a['session_key'], b['session_id'], 'read me')['message_id']
    d.acknowledge(b['session_key'], ack_message_id)
    ack_event = next(e for e in d.events_page('alpha', limit=50)['events'] if e['kind'] == 'message.acknowledged')
    assert ack_event['text'] == f'read {ack_message_id}'
    next_before = page['next_before']
    page2 = d.events_page('alpha', before=next_before, limit=50)
    assert all(e['seq'] < next_before for e in page2['events'])


def test_events_page_unknown_kind_falls_back(desk):
    d, a, b = desk
    with d.connection(True) as c:
        d.event(c, 'alpha', a['session_id'], 'made.up.kind', {'x': 1})
    events = d.events_page('alpha', limit=50)['events']
    made_up = next(e for e in events if e['kind'] == 'made.up.kind')
    assert made_up['text'].startswith('made.up.kind ')


# ---- GET /api/lanes ------------------------------------------------------------------

def test_lanes_bar_from_claim_to_done(desk):
    d, a, b = desk
    t = fresh(d, d.claim(a['session_key'], 'Lane task', ['src/lane'], 'x'))
    lanes = d.lanes('alpha', hours=12)
    session = next(s for s in lanes['sessions'] if s['id'] == a['session_id'])
    assert session['bars'][0]['task_id'] == t['id'] and session['bars'][0]['kind'] == 'run'
    d.update(a['session_key'], t['id'], t['version'], 'DONE', 'x', 'done', 'ok')
    lanes2 = d.lanes('alpha', hours=12)
    session2 = next(s for s in lanes2['sessions'] if s['id'] == a['session_id'])
    assert session2['bars'][0]['kind'] == 'done'
    assert lanes2['start'] < lanes2['end']


def test_lanes_blocked_and_stale_bar_kinds(desk):
    d, a, b = desk
    t = fresh(d, d.claim(a['session_key'], 'T', ['src/t'], 'x'))
    d.update(a['session_key'], t['id'], t['version'], 'BLOCKED', 'waiting')
    lanes = d.lanes('alpha', hours=12)
    session = next(s for s in lanes['sessions'] if s['id'] == a['session_id'])
    assert session['bars'][0]['kind'] == 'blocked'
    backdate_session(d, a['session_id'], 7)
    lanes2 = d.lanes('alpha', hours=12)
    session2 = next(s for s in lanes2['sessions'] if s['id'] == a['session_id'])
    # once unblocked and stale it would show 'stale'; still BLOCKED here so stays 'blocked'
    assert session2['stale'] is True


def test_lanes_marks(desk):
    d, a, b = desk
    t = fresh(d, d.claim(a['session_key'], 'T', ['src/t'], 'x'))
    d.record_deploy(a['session_key'], 'web', 'abc123', 'shipped it', t['id'])
    d.ask(a['session_key'], 'human', 'ok?', t['id'])
    d.request_approval(a['session_key'], 'Ship?', ['y', 'n'], 'ctx', t['id'])
    lanes = d.lanes('alpha', hours=12)
    session = next(s for s in lanes['sessions'] if s['id'] == a['session_id'])
    mark_kinds = {m['kind'] for m in session['marks']}
    assert {'deploy', 'question', 'approval'} <= mark_kinds


def test_lanes_only_sessions_with_a_bar_or_mark_in_window(desk):
    d, a, b = desk
    lanes = d.lanes('alpha', hours=1)
    assert lanes['sessions'] == []
    d.claim(a['session_key'], 'T', ['src/t'], 'x')
    lanes2 = d.lanes('alpha', hours=1)
    assert any(s['id'] == a['session_id'] for s in lanes2['sessions'])
    assert not any(s['id'] == b['session_id'] for s in lanes2['sessions'])
