"""Action items: the notes an agent ends a task or a turn with, saved as
checkable items linked to the project, the task and the session."""
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
    repo = declared(tmp_path, 'alpha')
    a = d.register('alpha builder', 'claude', '', 'main', repo)
    b = d.register('alpha reviewer', 'codex', '', 'main', repo)
    return d, a, b


def fresh(d, t):
    with d.connection() as c:
        return d.task(c, t['id'])


def test_finishing_a_task_saves_what_is_left_for_the_human(desk):
    d, a, b = desk
    t = fresh(d, d.claim(a['session_key'], 'Ship the feature', ['src/feature'], 'Build'))
    d.update(a['session_key'], t['id'], t['version'], 'DONE', 'x', 'shipped', 'tests green',
             action_items=['Reassign the 14 stale claims on the dashboard', 'Decide whether to push to GitHub'])
    board = d.snapshot('alpha')['action_items']
    assert sorted(i['body'] for i in board) == ['Decide whether to push to GitHub',
                                                 'Reassign the 14 stale claims on the dashboard']
    item = board[0]
    assert item['assignee'] == 'rohan' and item['assignee_name'] == 'Human' and item['status'] == 'open'
    assert item['task_id'] == t['id'] and item['task_title'] == 'Ship the feature'
    assert item['author'] == a['session_id'] and item['author_name'] == 'alpha builder'
    context = d.get_task_context(b['session_key'], t['id'])
    assert len(context['action_items']) == 2
    assert context['journal'][-1]['kind'] == 'action'


def test_items_for_agents_and_for_one_session(desk):
    d, a, b = desk
    t = d.claim(a['session_key'], 'T', ['src/t'], 'x')
    d.add_action_items(a['session_key'], ['Refresh the onboarding kit after the next restart'], t['id'], 'agents')
    d.add_action_items(a['session_key'], ['Review src/feature before merge'], t['id'], b['session_id'])
    for_b = d.check_in(b['session_key'], include=['action_items'])['action_items']['for_me']
    assert sorted(i['body'] for i in for_b) == ['Refresh the onboarding kit after the next restart',
                                                'Review src/feature before merge']
    assert d.check_in(b['session_key'], include=['counts'])['counts']['open_action_items_for_me'] == 2
    written = d.check_in(a['session_key'], include=['action_items'])['action_items']['written_by_me']
    assert len(written) == 2


def test_resolving_and_who_may(desk, tmp_path):
    d, a, b = desk
    outsider = d.register('beta agent', 'claude', '', 'main', declared(tmp_path, 'beta'))
    t = d.claim(a['session_key'], 'T', ['src/t'], 'x')
    human_item = d.add_action_items(a['session_key'], ['Approve the budget'], t['id'])['items'][0]
    mine_for_b = d.add_action_items(a['session_key'], ['Check the logs'], t['id'], b['session_id'])['items'][0]
    with pytest.raises(PermissionError):
        d.resolve_action_item(outsider['session_key'], mine_for_b['id'])
    with pytest.raises(PermissionError):   # b neither wrote nor holds the human's item
        d.resolve_action_item(b['session_key'], human_item['id'])
    done = d.resolve_action_item(b['session_key'], mine_for_b['id'], 'done', 'logs clean')
    assert done['status'] == 'done' and done['resolved_by'] == b['session_id'] and done['resolution'] == 'logs clean'
    assert d.check_in(b['session_key'], include=['action_items'])['action_items']['for_me'] == []
    # the author can drop its own item; the human ticks from the dashboard
    assert d.resolve_action_item(a['session_key'], human_item['id'], 'dropped', 'no longer needed')['status'] == 'dropped'
    reopened = d.human('alpha', 'action.resolve', {'item_id': human_item['id'], 'status': 'open'})
    assert reopened['status'] == 'open' and reopened['resolved'] is None
    ticked = d.human('alpha', 'action.resolve', {'item_id': human_item['id']})
    assert ticked['status'] == 'done' and ticked['resolved_by_name'] == 'Human'
    with pytest.raises(ValueError):
        d.resolve_action_item(a['session_key'], human_item['id'], 'maybe')


def test_cross_project_items_land_on_the_other_board(desk, tmp_path):
    d, a, b = desk
    outsider = d.register('beta agent', 'claude', '', 'main', declared(tmp_path, 'beta'))
    t = d.claim(a['session_key'], 'T', ['src/t'], 'x')
    item = d.add_action_items(a['session_key'], ['Commit the regenerated Project Desk blocks'], t['id'],
                              assignee='beta:agents')['items'][0]
    assert item['project'] == 'beta' and item['origin_project'] == 'alpha'
    assert [i['id'] for i in d.check_in(outsider['session_key'], include=['action_items'])['action_items']['for_me']] == [item['id']]
    assert any(i['id'] == item['id'] for i in d.snapshot('alpha')['action_items'])   # the sender sees it too
    assert d.resolve_action_item(outsider['session_key'], item['id'])['status'] == 'done'
    with pytest.raises(ValueError, match='Unknown project'):
        d.add_action_items(a['session_key'], ['x'], t['id'], assignee='nowhere:agents')
    with pytest.raises(ValueError, match='human'):
        d.add_action_items(a['session_key'], ['x'], t['id'], assignee='beta:everyone')


def test_validation_and_duplicates(desk, tmp_path):
    d, a, b = desk
    t = d.claim(a['session_key'], 'T', ['src/t'], 'x')
    first = d.add_action_items(a['session_key'], ['Same line'], task_id=t['id'])['items']
    again = d.add_action_items(a['session_key'], ['Same line'], task_id=t['id'])['items']
    assert len(first) == 1 and again == []
    for bad in ([], ['x'] * 21, [''], 'x' * 700):
        with pytest.raises(ValueError):
            d.add_action_items(a['session_key'], bad, t['id'])
    other = d.register('beta agent', 'claude', '', 'main', declared(tmp_path, 'beta'))
    beta_task = d.claim(other['session_key'], 'Beta', ['src'], 'x')
    with pytest.raises(ValueError):
        d.add_action_items(a['session_key'], ['x'], task_id=beta_task['id'])
    t = fresh(d, t)
    with pytest.raises(ValueError):
        d.update(a['session_key'], t['id'], t['version'], 'RUNNING', 'x', action_items='not a list')


def test_dashboard_adds_and_ticks(tmp_path, monkeypatch):
    from server import create_app
    monkeypatch.setenv('PROJECT_DESK_STRICT', '1')
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'ROSTER.md')
    store = app.state.store
    store.register('alpha', 'claude', '', 'main', declared(tmp_path, 'alpha'))
    headers = {'X-Project-Desk': 'dashboard'}
    with TestClient(app) as client:
        added = client.post('/api/action', headers=headers, json={'project': 'alpha', 'action': 'action.add',
                            'data': {'body': '- [ ] Call the vendor\n- [ ] Renew the certificate\n'}})
        assert added.status_code == 200
        items = added.json()['items']
        assert [i['body'] for i in items] == ['Call the vendor', 'Renew the certificate']
        ticked = client.post('/api/action', headers=headers, json={'project': 'alpha', 'action': 'action.resolve',
                             'data': {'item_id': items[0]['id']}})
        assert ticked.status_code == 200 and ticked.json()['status'] == 'done'
        state = client.get('/api/state?project=alpha').json()
        assert {i['body']: i['status'] for i in state['action_items']} == {'Call the vendor': 'done',
                                                                           'Renew the certificate': 'open'}


def test_mcp_tools(tmp_path, monkeypatch):
    from server import create_app
    monkeypatch.setenv('PROJECT_DESK_STRICT', '1')
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'ROSTER.md')
    a = app.state.store.register('alpha', 'claude', '', 'main', declared(tmp_path, 'alpha'))

    def call(client, name, arguments):
        response = client.post('/mcp', headers={'Accept': 'application/json, text/event-stream'}, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})
        result = response.json()['result']
        assert not result.get('isError'), result
        return result.get('structuredContent') or json.loads(result['content'][0]['text'])

    t = app.state.store.claim(a['session_key'], 'T', ['src/t'], 'x')
    with TestClient(app) as client:
        made = call(client, 'add_action_items', {'session_key': a['session_key'], 'items': ['Left for you: restart'],
                                                 'task_id': t['id']})
        item = made['items'][0]
        assert call(client, 'resolve_action_item', {'session_key': a['session_key'], 'item_id': item['id']})['status'] == 'done'
        more = call(client, 'add_action_items', {'session_key': a['session_key'], 'items': ['One', 'Two'],
                                                 'task_id': t['id']})['items']
        many = call(client, 'resolve_action_item', {'session_key': a['session_key'],
                                                    'item_ids': [i['id'] for i in more] + ['ai-nope']})
        assert sorted(many['resolved']) == sorted(i['id'] for i in more) and list(many['failed']) == ['ai-nope']


# ---- kept per task, so they cannot pile up ----------------------------------------

def open_bodies(d, project='alpha'):
    return sorted(i['body'] for i in d.snapshot(project)['action_items'] if i['status'] == 'open')


def test_an_agent_must_name_the_task_its_items_come_from(desk):
    d, a, b = desk
    with pytest.raises(ValueError, match='per task'):
        d.add_action_items(a['session_key'], ['Decide the price'])
    # the human's own to-do lines from the dashboard need no task
    assert d.human('alpha', 'action.add', {'body': 'Call the vendor'})['items'][0]['task_id'] is None


def test_a_tasks_new_list_replaces_its_earlier_one(desk):
    d, a, b = desk
    t = fresh(d, d.claim(a['session_key'], 'Ship it', ['src/ship'], 'Build'))
    other = d.claim(a['session_key'], 'Other', ['src/other'], 'Build')
    d.update(a['session_key'], t['id'], t['version'], 'RUNNING', 'x', action_items=['Pick the group', 'Approve the price'])
    d.add_action_items(a['session_key'], ['Keep: from another task'], other['id'])
    d.add_action_items(a['session_key'], ['For agents: rerun the bench'], t['id'], 'agents')
    d.human('alpha', 'action.add', {'body': 'My own note', 'task_id': t['id']})
    t = fresh(d, t)
    d.update(a['session_key'], t['id'], t['version'], 'RUNNING', 'x', action_items=['Approve the price', 'Say go'])
    assert open_bodies(d) == ['Approve the price', 'For agents: rerun the bench', 'Keep: from another task',
                              'My own note', 'Say go']
    board = {i['body']: i for i in d.snapshot('alpha')['action_items']}
    assert board['Pick the group']['status'] == 'superseded'
    assert board['Pick the group']['resolution'] == 'Replaced by a newer list for this task'
    assert board['Approve the price']['task_status'] == 'RUNNING'
    journal = [e['entry'] for e in d.get_task_context(a['session_key'], t['id'])['journal'] if e['kind'] == 'action']
    assert journal[-2:] == ['1 earlier action item(s) replaced by the new list', '1 action item(s) for Human: Say go']
    t = fresh(d, t)   # omitted: the list stays as it is
    d.update(a['session_key'], t['id'], t['version'], 'RUNNING', 'y')
    assert 'Say go' in open_bodies(d)
    t = fresh(d, t)   # an empty list clears the agents' items for the human; the human's own note stays
    d.update(a['session_key'], t['id'], t['version'], 'DONE', 'x', 'shipped', 'tests green', action_items=[])
    assert open_bodies(d) == ['For agents: rerun the bench', 'Keep: from another task', 'My own note']
    assert {i['task_status'] for i in d.snapshot('alpha')['action_items'] if i['task_id'] == t['id']} == {'DONE'}


def test_the_human_clears_many_at_once(desk):
    d, a, b = desk
    t = d.claim(a['session_key'], 'T', ['src/t'], 'x')
    items = d.add_action_items(a['session_key'], ['One', 'Two', 'Three'], t['id'])['items']
    result = d.human('alpha', 'action.clear', {'item_ids': [items[0]['id'], items[1]['id'], 'ai-missing']})
    assert sorted(result['resolved']) == sorted([items[0]['id'], items[1]['id']])
    assert list(result['failed']) == ['ai-missing'] and result['status'] == 'dropped'
    assert open_bodies(d) == ['Three']
    cleared = {i['body']: i for i in d.snapshot('alpha')['action_items']}['One']
    assert cleared['status'] == 'dropped' and cleared['resolution'] == 'Cleared from the dashboard'
    reopened = d.human('alpha', 'action.resolve', {'item_id': items[0]['id'], 'status': 'open'})
    assert reopened['status'] == 'open'
    with pytest.raises(ValueError):
        d.human('alpha', 'action.clear', {'item_ids': []})
    with pytest.raises(ValueError):
        d.human('alpha', 'action.clear', {'item_ids': [items[2]['id']], 'status': 'maybe'})


def test_an_agent_cannot_clear_what_is_not_its_own(desk, tmp_path):
    d, a, b = desk
    t = d.claim(a['session_key'], 'T', ['src/t'], 'x')
    items = d.add_action_items(a['session_key'], ['One', 'Two'], t['id'])['items']
    result = d.resolve_action_item(b['session_key'], item_ids=[i['id'] for i in items])
    assert result['resolved'] == [] and len(result['failed']) == 2
    assert open_bodies(d) == ['One', 'Two']
