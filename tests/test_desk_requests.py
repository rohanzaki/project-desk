"""Desk feature requests: agents ask, the human approves, then one willing agent builds it."""
import json

import pytest

from deskcore import Conflict
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
    a = d.register('alpha asker', 'claude', '', 'main', declared(tmp_path, 'alpha'))
    b = d.register('alpha builder', 'codex', '', 'main', declared(tmp_path, 'alpha'))
    x = d.register('beta builder', 'claude', '', 'main', declared(tmp_path, 'beta'))
    return d, a, b, x


def test_an_agent_asks_and_sees_similar_open_requests(desk):
    d, a, b, x = desk
    first = d.request_desk_feature(a['session_key'], 'Snooze stale claim alerts', 'Stale claim alerts repeat every hour for tasks I know about')
    assert first['request']['status'] == 'proposed' and first['request']['origin_project'] == 'alpha'
    assert first['similar'] == []
    second = d.request_desk_feature(x['session_key'], 'Mute stale claim alerts per task', 'The stale claim alerts repeat for known tasks')
    assert [s['id'] for s in second['similar']] == [first['request']['id']]
    assert 'support_desk_request' in second['note']


def test_nobody_can_volunteer_before_the_human_approves(desk):
    d, a, b, x = desk
    rid = d.request_desk_feature(a['session_key'], 'Show deploy queue on the board', 'I poll prod_state')['request']['id']
    with pytest.raises(PermissionError, match='not approved'):
        d.volunteer_desk_request(b['session_key'], rid)
    assert d.desk_requests()[0]['status'] == 'proposed'
    assert d.desk_requests()[0]['task_id'] is None


def test_approval_tells_every_project_and_one_volunteer_wins(desk):
    d, a, b, x = desk
    rid = d.request_desk_feature(a['session_key'], 'Show deploy queue on the board', 'I poll prod_state')['request']['id']
    d.human('alpha', 'desk_request.approve', {'request_id': rid, 'note': 'yes, keep it small'})
    beta_inbox = d.check_in(x['session_key'], include=['inbox'])['inbox']
    assert any(f"DESK REQUEST APPROVED {rid}" in m['body'] for m in beta_inbox)
    got = d.volunteer_desk_request(x['session_key'], rid)   # another project may build it
    assert got['request']['status'] == 'in_progress' and got['request']['volunteer'] == x['session_id']
    assert got['task']['resources'] == ['service:project-desk-dev'] and got['task']['project'] == 'beta'
    assert 'Restarting Project Desk' in got['checklist'] and 'Bidder' in got['checklist']
    with pytest.raises(Conflict, match='in_progress'):
        d.volunteer_desk_request(b['session_key'], rid)
    with pytest.raises(Conflict):
        d.human('alpha', 'desk_request.approve', {'request_id': rid})


def test_one_desk_change_at_a_time_and_a_failed_claim_gives_the_request_back(desk):
    d, a, b, x = desk
    r1 = d.request_desk_feature(a['session_key'], 'First change', 'why one')['request']['id']
    r2 = d.request_desk_feature(a['session_key'], 'Second change', 'why two')['request']['id']
    for rid in (r1, r2):
        d.human('alpha', 'desk_request.approve', {'request_id': rid})
    d.volunteer_desk_request(b['session_key'], r1)
    with pytest.raises(Conflict, match='service:project-desk-dev'):
        d.volunteer_desk_request(x['session_key'], r2)
    back = {r['id']: r for r in d.desk_requests()}[r2]
    assert back['status'] == 'approved' and back['volunteer'] is None


def test_the_request_is_done_when_its_task_is(desk):
    d, a, b, x = desk
    rid = d.request_desk_feature(a['session_key'], 'Compact digest', 'why')['request']['id']
    d.human('alpha', 'desk_request.approve', {'request_id': rid})
    task = d.volunteer_desk_request(b['session_key'], rid)['task']
    with d.connection() as c:
        task = d.task(c, task['id'])
    d.update(b['session_key'], task['id'], task['version'], 'DONE', 'x', 'Shipped the digest', 'tests green', commit_ref='abc123')
    done = d.desk_requests()[0]
    assert done['status'] == 'done' and done['done_commit'] == 'abc123'
    assert d.desk_requests(status='done')[0]['id'] == rid


def test_reject_tells_the_author_support_and_withdraw(desk):
    d, a, b, x = desk
    rid = d.request_desk_feature(a['session_key'], 'Big rewrite', 'why')['request']['id']
    supported = d.support_desk_request(x['session_key'], rid, 'Bidder needs it too')
    assert supported['support_count'] == 1 and supported['support'][0]['note'] == 'Bidder needs it too'
    d.human('alpha', 'desk_request.reject', {'request_id': rid, 'note': 'not now'})
    assert any('not approved' in m['body'] and 'not now' in m['body'] for m in d.check_in(a['session_key'], include=['inbox'])['inbox'])
    with pytest.raises(Conflict):
        d.support_desk_request(x['session_key'], rid)
    other = d.request_desk_feature(a['session_key'], 'Tiny tweak', 'why')['request']['id']
    with pytest.raises(PermissionError):
        d.withdraw_desk_request(b['session_key'], other)
    assert d.withdraw_desk_request(a['session_key'], other, 'duplicate')['status'] == 'withdrawn'


def test_proposed_requests_wait_in_needs_you(desk):
    d, a, b, x = desk
    rid = d.request_desk_feature(a['session_key'], 'Snooze per task', 'why')['request']['id']
    with d.connection() as c:
        items = d._request_attention(c)
    assert [i['key'] for i in items] == [f'desk_request:{rid}'] and items[0]['kind'] == 'desk_request'
    d.human('alpha', 'desk_request.approve', {'request_id': rid})
    with d.connection() as c:
        assert d._request_attention(c) == []
