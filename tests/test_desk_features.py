"""Memory and coordination: lessons, journal, resume, inbox triage, questions,
approvals, wait_for, queue + deploys, contracts, evidence, stale alerts, reopen."""
import json
import sqlite3
import threading
import time

import pytest
from starlette.testclient import TestClient

from store import Store, Conflict


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
    a = d.register('alpha api agent', 'claude', '', 'main', repo)
    b = d.register('alpha reviewer', 'codex', '', 'main', repo)
    return d, a, b, repo


def inbox(d, who):
    return d.check_in(who['session_key'], include=['inbox'])['inbox']


def age(d, session, minutes=0, hours=0):
    """Make a session look quiet for a while."""
    from datetime import datetime, timedelta, timezone
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes, hours=hours)).isoformat()
    with d.connection(True) as c:
        c.execute('UPDATE sessions SET last_seen=? WHERE id=?', (stamp, session['session_id']))


def fresh(d, t):
    with d.connection() as c:
        return d.task(c, t['id'])


# ---------- 1. lessons ----------

def test_remember_and_recall_by_words_paths_and_tags(desk):
    d, a, b, _ = desk
    lesson = d.remember(a['session_key'], 'pm2 restart ships no code: rsync the source first, then restart.',
                        paths=['predictor'], tags=['deploy', 'pm2'])
    assert lesson['id'].startswith('l-') and lesson['paths'] == ['predictor'] and lesson['scope'] == 'project'
    d.remember(a['session_key'], 'Finance tolerance lives in the catalogue, not the verifier.', paths=['src/lib/finance'])
    assert [l['id'] for l in d.recall(b['session_key'], 'restart rsync')['lessons']] == [lesson['id']]
    # every word first, then any word
    assert [l['id'] for l in d.recall(b['session_key'], 'restart nonsenseword')['lessons']] == [lesson['id']]
    assert [l['id'] for l in d.recall(b['session_key'], paths=['predictor/feeds.mjs'])['lessons']] == [lesson['id']]
    assert [l['id'] for l in d.recall(b['session_key'], tags=['pm2'])['lessons']] == [lesson['id']]
    assert len(d.recall(b['session_key'])['lessons']) == 2
    assert d.remember(a['session_key'], lesson['body'])['duplicate'] is True
    with pytest.raises(ValueError):
        d.remember(a['session_key'], 'x', tags=['Bad Tag!'])
    with pytest.raises(ValueError):
        d.remember(a['session_key'], 'x', paths=['/abs/path'])


def test_claim_and_would_conflict_hand_over_the_lessons_for_those_paths(desk):
    d, a, b, _ = desk
    broad = d.remember(a['session_key'], 'Everything under src needs the Linux worktree.', paths=['src'])
    narrow = d.remember(a['session_key'], 'Finance V2 screens use the DS navy tokens.', paths=['src/app/finance/v2'])
    d.remember(a['session_key'], 'Unrelated lesson.', paths=['docs'])
    d.remember(a['session_key'], 'A lesson with no paths is found by recall only.')
    planned = d.would_conflict(b['session_key'], ['src/app/finance/v2/page.tsx'])
    assert [l['id'] for l in planned['lessons']] == [narrow['id'], broad['id']]
    task = d.claim(b['session_key'], 'Finance screen', ['src/app/finance/v2'], 'Build')
    assert [l['id'] for l in task['lessons']] == [narrow['id'], broad['id']]
    assert [l['id'] for l in d.claim(b['session_key'], 'Deploy lane', ['service:cmu'], 'x')['lessons']] == []


def test_global_lessons_cross_projects_and_project_lessons_do_not(desk, tmp_path):
    d, a, b, _ = desk
    other = d.register('beta agent', 'claude', '', 'main', declared(tmp_path, 'beta'))
    d.remember(a['session_key'], 'Alpha-only fact.')
    shared = d.remember(a['session_key'], 'Never run pm2 kill on any server.', scope='global')
    assert [l['id'] for l in d.recall(other['session_key'])['lessons']] == [shared['id']]


def test_forget_archives(desk):
    d, a, b, _ = desk
    lesson = d.remember(a['session_key'], 'KSE-100 comes from the PSX timeseries endpoint.')
    d.forget(b['session_key'], lesson['id'], 'PSX retired that endpoint on 2026-09-26')
    assert d.recall(a['session_key'], 'KSE')['lessons'] == []
    assert d.snapshot('alpha')['lessons'] == []


# ---------- 2. journal ----------

def test_the_journal_records_the_life_of_a_task(desk):
    d, a, b, _ = desk
    t = d.claim(a['session_key'], 'Tender API', ['src/api'], 'Build the endpoint')
    d.log_progress(a['session_key'], t['id'], 'Found: the Bidder feed sends Urdu dates; normalising in parse.ts')
    with pytest.raises(PermissionError):
        d.log_progress(b['session_key'], t['id'], 'not mine')
    t = fresh(d, t)
    d.update(a['session_key'], t['id'], t['version'], 'BLOCKED', 'Waiting on the Bidder schema')
    journal = d.get_task_context(b['session_key'], t['id'])['journal']
    assert [e['kind'] for e in journal] == ['claimed', 'progress', 'blocked']
    assert 'Urdu dates' in journal[1]['entry'] and journal[1]['author_name'] == 'alpha api agent'
    events = d.check_in(a['session_key'], include=['events'])['events']
    assert any(e['kind'] == 'journal.logged' for e in events)
    assert d.snapshot('alpha')['task_logs'][t['id']][-1]['kind'] == 'blocked'
    assert len(d.snapshot('alpha')['task_logs'][t['id']]) == 2


# ---------- 3. resume ----------

def test_resume_takes_back_an_earlier_sessions_work(desk):
    d, a, b, repo = desk
    t = d.claim(a['session_key'], 'Tender API', ['src/api'], 'Build')
    d.message(b['session_key'], a['session_id'], 'For the old session: schema is in docs/api.md')
    age(d, a, minutes=45)
    again = d.register('alpha api agent (after restart)', 'claude', '', 'main', repo)
    assert [r['session_id'] for r in again['resumable']] == [a['session_id']]
    assert again['resumable'][0]['tasks'][0]['id'] == t['id'] and 'resume_session' in again['resume_hint']
    resumed = d.resume_session(again['session_key'], a['session_id'], 'VS Code restarted')
    assert [x['id'] for x in resumed['tasks']] == [t['id']] and resumed['tasks'][0]['owner'] == again['session_id']
    assert resumed['tasks'][0]['journal'][-1]['kind'] == 'resumed'
    # the old session's mail is now this session's mail, and it can be acknowledged
    mail = inbox(d, again)
    assert any('schema is in docs/api.md' in m['body'] for m in mail)
    old_mid = next(m['id'] for m in mail if 'schema' in m['body'])
    assert d.acknowledge(again['session_key'], old_mid)['acknowledged_by'] == again['session_id']
    # the old identity is told, in case it was only idle
    with d.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM messages WHERE recipient=? AND body LIKE 'RESUMED:%'",
                         (a['session_id'],)).fetchone()[0] == 1
    assert any(m['recipient'] == 'rohan' and m['body'].startswith('RESUMED:') for m in d.snapshot('alpha')['messages'])
    with pytest.raises(Conflict, match='already resumed'):
        d.resume_session(again['session_key'], a['session_id'])
    t = fresh(d, t)
    assert d.update(again['session_key'], t['id'], t['version'], 'RUNNING', 'carry on')['owner'] == again['session_id']


def test_resume_rules(desk, tmp_path):
    d, a, b, repo = desk
    d.claim(a['session_key'], 'Tender API', ['src/api'], 'Build')
    newer = d.register('alpha api agent 2', 'claude', '', 'main', repo)
    assert 'resumable' not in newer   # a is still fresh
    with pytest.raises(Conflict, match='may still be running'):
        d.resume_session(newer['session_key'], a['session_id'])
    age(d, a, minutes=45)
    with pytest.raises(PermissionError):   # a codex session cannot resume a claude session
        d.resume_session(b['session_key'], a['session_id'])
    elsewhere = d.register('same kind, other worktree', 'claude', '', 'main', declared(tmp_path, 'alpha2'))
    with pytest.raises((PermissionError, ValueError)):
        d.resume_session(elsewhere['session_key'], a['session_id'])
    other_branch = d.register('same worktree, other branch', 'claude', '', 'feat/other', repo)
    assert 'resumable' not in other_branch
    with pytest.raises(PermissionError, match='branch'):
        d.resume_session(other_branch['session_key'], a['session_id'])


# ---------- 4. inbox triage ----------

def test_digest_puts_mail_for_you_first_and_names_kinds(desk):
    d, a, b, _ = desk
    d.message(b['session_key'], 'all', 'DEPLOY STARTING 08:14 PKT (s-x): SynthBot round 8.\nPlease hold.')
    d.message(b['session_key'], 'all', 'HEADS-UP: the desk restarts in 10 min.')
    direct = d.message(b['session_key'], a['session_id'], 'Can you review src/api before I merge?')
    digest = d.check_in(a['session_key'], include=['inbox_digest'])['inbox_digest']
    assert digest[0]['id'] == direct['message_id'] and digest[0]['to'] == 'you'
    assert [m['kind'] for m in digest[1:]] == ['deploy', 'fyi']
    assert digest[1]['first_line'] == 'DEPLOY STARTING 08:14 PKT (s-x): SynthBot round 8.'
    counts = d.check_in(a['session_key'], include=['counts'])['counts']
    assert (counts['unread_messages'], counts['unread_direct'], counts['unread_broadcast']) == (3, 1, 2)


def test_legacy_messages_get_an_inferred_kind(desk):
    d, a, b, _ = desk
    with d.connection(True) as c:   # a message written by the previous release: no meta row
        c.execute("INSERT INTO messages VALUES('m-legacy00001','alpha',?, 'all', 'MIGRATION ANNOUNCEMENT: x', NULL, '2026-01-01T00:00:00+00:00')",
                  (b['session_id'],))
    digest = d.check_in(a['session_key'], include=['inbox_digest'])['inbox_digest']
    assert digest[0]['kind'] == 'deploy'


def test_read_messages_then_acknowledge_broadcasts_in_one_call(desk, tmp_path):
    d, a, b, _ = desk
    outsider = d.register('beta', 'claude', '', 'main', declared(tmp_path, 'beta'))
    news = [d.message(b['session_key'], 'all', f'DEPLOY DONE {n}: released')['message_id'] for n in range(3)]
    fyi = d.message(b['session_key'], 'all', 'HEADS-UP: note')['message_id']
    direct = d.message(b['session_key'], a['session_id'], 'A question for you')['message_id']
    beta_msg = d.message(outsider['session_key'], 'all', 'beta only')['message_id']
    opened = d.read_messages(a['session_key'], [direct, news[0], beta_msg])
    assert [m['id'] for m in opened['messages']] == [direct, news[0]] and opened['refused'] == [beta_msg]
    cleared = d.acknowledge_inbox(a['session_key'], kinds=['deploy'])
    assert sorted(cleared['message_ids']) == sorted(news) and cleared['left_unread'] == 2
    cleared = d.acknowledge_inbox(a['session_key'])
    assert cleared['message_ids'] == [fyi]
    assert [m['id'] for m in inbox(d, a)] == [direct]   # mail for you is never swept
    assert d.acknowledge_inbox(a['session_key'], include_direct=True)['message_ids'] == [direct]
    with pytest.raises(ValueError):
        d.acknowledge_inbox(a['session_key'], kinds=['nonsense'])


def test_explicit_kind_is_validated(desk):
    d, a, b, _ = desk
    assert d.message(a['session_key'], 'all', 'plain words', kind='fyi')['status'] == 'sent'
    with pytest.raises(ValueError, match='kind'):
        d.message(a['session_key'], 'all', 'x', kind='shouting')


# ---------- 5. questions and approvals ----------

def test_a_question_stays_open_until_answered(desk):
    d, a, b, _ = desk
    q = d.ask(a['session_key'], b['session_id'], 'Does the tender feed send closing dates in PKT?')
    assert q['status'] == 'open'
    for_b = d.check_in(b['session_key'], include=['questions'])['questions']['for_me']
    assert [x['message_id'] for x in for_b] == [q['question_id']] and 'closing dates' in for_b[0]['question']
    assert d.check_in(b['session_key'], include=['counts'])['counts']['open_questions_for_me'] == 1
    # a reply that is not from an addressee does not close it
    d.message(a['session_key'], b['session_id'], 'bump', reply_to=q['question_id'])
    assert d.check_in(a['session_key'], include=['questions'])['questions']['mine'][0]['status'] == 'open'
    answer = d.message(b['session_key'], a['session_id'], 'Yes, PKT, as "DD-MM-YYYY HH:MM".', reply_to=q['question_id'])
    mine = d.check_in(a['session_key'], include=['questions'])['questions']['mine'][0]
    assert mine['status'] == 'answered' and mine['answer_message_id'] == answer['message_id'] and 'PKT' in mine['answer']
    got = [m for m in inbox(d, a) if m['id'] == answer['message_id']][0]
    assert got['kind'] == 'answer' and got['reply_to'] == q['question_id']
    with pytest.raises(ValueError):
        d.ask(a['session_key'], 'x-000000000000', 'to a crossover?')


def test_approval_round_trip(desk, tmp_path):
    d, a, b, _ = desk
    t = d.claim(a['session_key'], 'Tender API', ['src/api'], 'Build')
    approval = d.request_approval(a['session_key'], 'Which auth for the Bidder API?',
                                  ['HMAC shared secret', 'mTLS'], 'Bidder calls CMU server-to-server.', t['id'])
    assert approval['status'] == 'pending' and approval['options'] == ['HMAC shared secret', 'mTLS']
    board = d.snapshot('alpha')
    assert board['approvals'][0]['id'] == approval['id']
    human_mail = [m for m in board['messages'] if m['recipient'] == 'rohan']
    assert human_mail and human_mail[0]['kind'] == 'decision' and 'APPROVAL NEEDED' in human_mail[0]['body']
    with pytest.raises(ValueError):
        d.human('alpha', 'approval.decide', {'approval_id': approval['id'], 'decision': 'something else'})
    decided = d.human('alpha', 'approval.decide', {'approval_id': approval['id'], 'decision': 'mTLS',
                                                   'note': 'we already run a CA'})
    assert decided['status'] == 'decided' and decided['decision'] == 'mTLS'
    assert any(m['body'].startswith('DECISION ' + approval['id']) for m in inbox(d, a))
    assert any(n['kind'] == 'decision' and 'mTLS' in n['body'] for n in d.snapshot('alpha')['notes'])
    assert d.check_in(a['session_key'], include=['approvals'])['approvals'][0]['decision'] == 'mTLS'
    with pytest.raises(Conflict):
        d.human('alpha', 'approval.decide', {'approval_id': approval['id'], 'decision': 'mTLS'})
    with pytest.raises(ValueError):
        d.request_approval(a['session_key'], 't', ['only one'], 'c')


# ---------- 6. wait_for ----------

def test_wait_state_sees_messages_answers_decisions_resources_crossovers(desk):
    d, a, b, _ = desk
    session = d.wait_session(a['session_key'])
    held = d.claim(b['session_key'], 'Deploy', ['service:cmu-deploy'], 'deploying')
    before = d.wait_state(session, ['service:cmu-deploy'])
    assert before['holders'] == {'service:cmu-deploy': [held['id']]}
    d.message(b['session_key'], a['session_id'], 'done soon')
    held = fresh(d, held)
    d.update(b['session_key'], held['id'], held['version'], 'DONE', 'x', 'deployed', 'ok')
    changes = d.wait_changes(before, d.wait_state(session, ['service:cmu-deploy']))
    kinds = {c['kind'] for c in changes}
    assert kinds == {'message', 'resource'}
    assert next(c for c in changes if c['kind'] == 'resource')['free'] is True


def test_wait_for_over_mcp(tmp_path, monkeypatch):
    from server import create_app
    monkeypatch.setenv('PROJECT_DESK_STRICT', '1')
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'ROSTER.md')
    store = app.state.store
    repo = declared(tmp_path, 'alpha')
    a = store.register('alpha', 'claude', '', 'main', repo)
    b = store.register('beta', 'codex', '', 'main', repo)

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
        assert {'remember', 'recall', 'forget', 'log_progress', 'resume_session', 'reopen_task', 'read_messages',
                'acknowledge_inbox', 'ask', 'request_approval', 'queue_for', 'record_deploy', 'prod_state',
                'wait_for', 'crossover_contract'} <= names
        timed_out = call(client, 'wait_for', {'session_key': a['session_key'], 'timeout': 1})
        assert timed_out['timed_out'] is True and timed_out['changed'] == []
        threading.Timer(1.5, lambda: store.message(b['session_key'], a['session_id'], 'here you go')).start()
        started = time.time()
        woke = call(client, 'wait_for', {'session_key': a['session_key'], 'timeout': 20})
        assert woke['changed'][0]['kind'] == 'message' and time.time() - started < 10
        lesson = call(client, 'remember', {'session_key': a['session_key'], 'body': 'A lesson over MCP',
                                           'paths': ['src']})
        assert call(client, 'recall', {'session_key': b['session_key'], 'query': 'lesson'})['lessons'][0]['id'] == lesson['id']


# ---------- 7. queue and deploy record ----------

def test_queue_hands_the_lane_to_the_next_in_line(desk):
    d, a, b, _ = desk
    held = d.claim(a['session_key'], 'CMU deploy', ['service:cmu-deploy'], 'deploying')
    queued = d.queue_for(b['session_key'], 'service:cmu-deploy', 'Logistics release')
    assert queued['position'] == 1 and queued['free_now'] is False and queued['held_by'][0]['task_id'] == held['id']
    assert d.would_conflict(b['session_key'], ['service:cmu-deploy'])['queues']['service:cmu-deploy'][0]['session_id'] == b['session_id']
    held = fresh(d, held)
    d.update(a['session_key'], held['id'], held['version'], 'DONE', 'x', 'deployed', 'ok')
    turn = [m for m in inbox(d, b) if m['body'].startswith('YOUR TURN')]
    assert turn and turn[0]['kind'] == 'queue' and turn[0]['sender'] == 'project-desk'
    d.claim(b['session_key'], 'Logistics deploy', ['service:cmu-deploy'], 'deploying')
    assert d.check_in(b['session_key'], include=['queues'])['queues'] == []


def test_a_lapsed_turn_passes_to_the_next(desk, tmp_path):
    d, a, b, repo = desk
    c3 = d.register('third', 'claude', '', 'main', repo)
    held = d.claim(a['session_key'], 'deploy', ['service:lane'], 'x')
    d.queue_for(b['session_key'], 'service:lane')
    d.queue_for(c3['session_key'], 'service:lane')
    held = fresh(d, held)
    d.update(a['session_key'], held['id'], held['version'], 'DONE', 'x', 'done', 'ok')
    with d.connection(True) as c:
        c.execute("UPDATE resource_queue SET notified='2000-01-01T00:00:00+00:00' WHERE session_id=?", (b['session_id'],))
    d.tend_queues()
    assert any(m['body'].startswith('QUEUE: your turn') for m in inbox(d, b))
    assert any(m['body'].startswith('YOUR TURN') for m in inbox(d, c3))
    assert d.queue_for(c3['session_key'], 'service:lane', leave=True)['left'] is True


def test_deploys_are_recorded_and_prod_state_reports_them(desk):
    d, a, b, _ = desk
    t = d.claim(a['session_key'], 'SynthBot round 9', ['service:cmu', 'src/lib/synthbot'], 'deploy')
    t = fresh(d, t)
    d.update(a['session_key'], t['id'], t['version'], 'DONE', 'x', 'shipped round 9', 'login 200',
             commit_ref='87f2c9c9', deployment='deployed')
    d.record_deploy(b['session_key'], 'insightwatch-mcp', 'a05b9f74', 'MCP 49 tools')
    prod = d.prod_state(b['session_key'], 'cmu')
    services = {s['service']: s for s in prod['services']}
    assert services['cmu']['commit_ref'] == '87f2c9c9' and services['cmu']['by'] == 'alpha api agent'
    assert services['insightwatch-mcp']['commit_ref'] == 'a05b9f74'
    assert prod['history'][0]['summary'] == 'shipped round 9'
    assert {p['service'] for p in d.snapshot('alpha')['prod']} == {'cmu', 'insightwatch-mcp'}


# ---------- 8. crossover contracts ----------

def test_a_new_contract_version_clears_sign_offs(desk, tmp_path):
    d, a, b, _ = desk
    beta = d.register('beta client', 'claude', '', 'main', declared(tmp_path, 'beta'))
    ta = d.claim(a['session_key'], 'Tender API', ['src/api'], 'Build')
    x = d.start_crossover(a['session_key'], ta['id'], [beta['session_id']])
    with pytest.raises(PermissionError, match='Join'):
        d.crossover_contract(beta['session_key'], x['id'], 'POST /tenders {...}')
    assert d.crossover_contract(beta['session_key'], x['id'])['version'] == 0
    d.join_crossover(beta['session_key'], x['id'], 'Build client', ['src/client'])
    v1 = d.crossover_contract(a['session_key'], x['id'], 'POST /tenders {"title": str, "closes": iso8601}')
    assert v1['version'] == 1
    view = d.sign_off_crossover(beta['session_key'], x['id'], 'client matches v1',
                                evidence=[{'command': 'npm test contract', 'exit_code': 0, 'tests_passed': 12}])
    assert next(m for m in view['members'] if m['project'] == 'beta')['signed_version'] == 1
    v2 = d.crossover_contract(a['session_key'], x['id'], 'POST /tenders {"title": str, "closes_at": iso8601}')
    assert v2['version'] == 2
    after = d.check_in(a['session_key'], include=['crossovers'])['crossovers'][0]
    assert after['contract_version'] == 2 and not any(m['signed_off'] for m in after['members'])
    assert any('CONTRACT v2' in m['body'] for m in inbox(d, beta))
    assert d.crossover_contract(beta['session_key'], x['id'])['body'].endswith('"closes_at": iso8601}')


# ---------- 9. evidence ----------

def test_structured_evidence_marks_a_task_checked_or_failing(desk):
    d, a, b, _ = desk
    good = d.claim(a['session_key'], 'Good', ['src/good'], 'x')
    bad = d.claim(a['session_key'], 'Bad', ['src/bad'], 'x')
    good, bad = fresh(d, good), fresh(d, bad)
    d.update(a['session_key'], good['id'], good['version'], 'DONE', 'x', 'done', 'suite green',
             evidence=[{'command': 'pytest -q', 'exit_code': 0, 'tests_passed': 210, 'tests_failed': 0, 'commit': 'abc123'}])
    d.update(a['session_key'], bad['id'], bad['version'], 'DONE', 'x', 'done', 'one known failure',
             evidence=[{'command': 'npm test', 'exit_code': 1, 'tests_passed': 40, 'tests_failed': 1}])
    board = d.snapshot('alpha')['evidence']
    assert board[good['id']] == {'checks': 1, 'failing': 0, 'level': 'checked'}
    assert board[bad['id']]['level'] == 'failing'
    assert d.get_task_context(b['session_key'], good['id'])['evidence'][0]['tests_passed'] == 210
    t = d.claim(a['session_key'], 'Other', ['src/other'], 'x')
    for wrong in ([{'command': 'x', 'surprise': 1}], [{'output': 'no command'}], [{'command': 'x', 'exit_code': 'zero'}], 'text'):
        with pytest.raises(ValueError):
            d.update(a['session_key'], t['id'], t['version'], 'RUNNING', 'x', evidence=wrong)


# ---------- 10. stale-claim alerts ----------

def test_stale_claims_are_reported_once_per_episode(desk):
    d, a, b, _ = desk
    t = d.claim(a['session_key'], 'Tender API', ['src/api'], 'Build')
    d.claim(b['session_key'], 'Fresh work', ['src/fresh'], 'Build')
    age(d, a, hours=7)
    assert [s['task_id'] for s in d.snapshot('alpha')['stale_claims']] == [t['id']]
    assert d.alert_stale_claims() == 1
    alerts = [m for m in d.snapshot('alpha')['messages'] if m['sender'] == 'project-desk' and m['recipient'] == 'rohan']
    assert len(alerts) == 1 and t['id'] in alerts[0]['body'] and alerts[0]['kind'] == 'stale'
    assert d.alert_stale_claims() == 0       # same episode: no repeat
    d.check_in(a['session_key'], include=['counts'])   # the owner comes back...
    age(d, a, hours=8)                                  # ...and goes quiet again
    assert d.alert_stale_claims() == 1


# ---------- 11. reopen ----------

def test_an_agent_reopens_a_finished_task_it_needs(desk, tmp_path):
    d, a, b, _ = desk
    d.remember(a['session_key'], 'The tender API rate-limits at 10 req/s.', paths=['src/api'])
    t = d.claim(a['session_key'], 'Tender API', ['src/api'], 'Build')
    t = fresh(d, t)
    d.update(a['session_key'], t['id'], t['version'], 'DONE', 'x', 'API shipped', 'curl 200')
    reopened = d.reopen_task(b['session_key'], t['id'], 'Bidder needs a closes_at field', 'Add closes_at')
    assert reopened['owner'] == b['session_id'] and reopened['status'] == 'RUNNING'
    assert reopened['lessons'][0]['body'].startswith('The tender API')
    assert d.would_conflict(a['session_key'], ['src/api'])['clear'] is False
    assert any(m['body'].startswith('REOPENED ' + t['id']) for m in inbox(d, a))
    journal = d.get_task_context(a['session_key'], t['id'])['journal']
    assert journal[-1]['kind'] == 'reopened' and 'API shipped' in journal[-1]['entry']
    with pytest.raises(Conflict, match='Only a completed task'):
        d.reopen_task(a['session_key'], t['id'], 'again', 'x')
    # paths someone else took since completion block the reopen
    done = d.claim(a['session_key'], 'Docs', ['docs'], 'x')
    done = fresh(d, done)
    d.update(a['session_key'], done['id'], done['version'], 'DONE', 'x', 'docs done', 'ok')
    d.claim(b['session_key'], 'New docs work', ['docs/api.md'], 'x')
    with pytest.raises(Conflict):
        d.reopen_task(a['session_key'], done['id'], 'need it back', 'x')
    outsider = d.register('beta', 'claude', '', 'main', declared(tmp_path, 'beta'))
    with pytest.raises(PermissionError):
        d.reopen_task(outsider['session_key'], done['id'], 'not my project', 'x')


# ---------- rollback safety and the dashboard ----------

def test_original_tables_keep_their_shape(desk):
    d, a, b, _ = desk
    expected = {
        'messages': ['id', 'project', 'sender', 'recipient', 'body', 'task_id', 'created'],
        'sessions': ['id', 'secret_hash', 'name', 'kind', 'project', 'branch', 'worktree', 'last_seen', 'imported'],
        'receipts': ['message_id', 'session_id', 'acknowledged'],
        'notes': ['id', 'project', 'author', 'kind', 'body', 'created'],
    }
    with sqlite3.connect(d.path) as c:
        for table, columns in expected.items():
            assert [r[1] for r in c.execute(f'PRAGMA table_info({table})')] == columns, table


def test_dashboard_actions_for_lessons_and_approvals(tmp_path, monkeypatch):
    from server import create_app
    monkeypatch.setenv('PROJECT_DESK_STRICT', '1')
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'ROSTER.md')
    store = app.state.store
    a = store.register('alpha', 'claude', '', 'main', declared(tmp_path, 'alpha'))
    approval = store.request_approval(a['session_key'], 'Pick one', ['A', 'B'], 'context')
    headers = {'X-Project-Desk': 'dashboard'}
    with TestClient(app) as client:
        made = client.post('/api/action', headers=headers, json={'project': 'alpha', 'action': 'lesson.create', 'data': {
            'body': 'Rohan: never deploy on Friday night.', 'paths': 'src/app\nsrc/lib', 'tags': 'deploy, rules'}})
        assert made.status_code == 200 and made.json()['paths'] == ['src/app', 'src/lib'] and made.json()['author'] == 'rohan'
        state = client.get('/api/state?project=alpha').json()
        assert state['lessons'][0]['body'].startswith('Rohan: never deploy')
        archived = client.post('/api/action', headers=headers, json={'project': 'alpha', 'action': 'lesson.archive',
                               'data': {'lesson_id': made.json()['id'], 'reason': 'test'}})
        assert archived.status_code == 200
        decided = client.post('/api/action', headers=headers, json={'project': 'alpha', 'action': 'approval.decide',
                              'data': {'approval_id': approval['id'], 'decision': 'B'}})
        assert decided.status_code == 200 and decided.json()['decision'] == 'B'


def test_hook_line_tags_the_kind(desk):
    from codex_hooks import collect
    d, a, b, _ = desk
    d.ask(b['session_key'], a['session_id'], 'Is src/api ready?')
    state = {'session_id': a['session_id'], 'project': 'alpha', 'agent': 'claude'}
    lines, _ = collect(state, d.check_in(a['session_key']), initial=True)
    assert any(l.startswith('UNACKNOWLEDGED MESSAGE') and '[question]' in l for l in lines)
