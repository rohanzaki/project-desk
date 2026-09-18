import pytest

from store import Conflict, Store


@pytest.fixture
def desk(tmp_path):
    return Store(tmp_path / 'desk.sqlite3')


def register(desk, name, kind='codex', project='media-intelligence'):
    return desk.register(name, kind, project, 'feat/handoff', f'/tmp/{name.lower()}')


def owned_task(desk, session, resource='src/handoff'):
    return desk.claim(session['session_key'], 'Handoff work', [resource], 'Finish the handoff')


def prepare(desk, owner, task, target):
    return desk.prepare_handoff(
        owner['session_key'], task['id'], task['version'], target['session_id'],
        'Implemented the bounded context API', 'Run the focused tests and review the UI',
        'pytest tests/test_handoff_context.py -q', 'The dashboard worker is editing static files',
        'abc123', 'feat/handoff', '/tmp/owner', ['store.py', 'server.py'])


def test_complete_handoff_is_stored_and_target_receives_context(desk):
    owner = register(desk, 'Owner')
    target = register(desk, 'Target', 'claude')
    task = owned_task(desk, owner)

    result = prepare(desk, owner, task, target)
    current = result['task']
    assert result['handoff_id'].startswith('h-')
    assert result['message_id'].startswith('m-')
    assert current['owner'] == owner['session_id']
    assert current['pending_owner'] == target['session_id']
    assert current['version'] == task['version'] + 1

    inbox = desk.check_in(target['session_key'])['inbox']
    assert [m['id'] for m in inbox] == [result['message_id']]
    context = desk.get_task_context(target['session_key'], task['id'])
    brief = context['latest_handoff']
    assert brief['id'] == result['handoff_id']
    assert brief['status'] == 'offered'
    assert brief['progress'].startswith('Implemented')
    assert brief['source']['id'] == owner['session_id']
    assert brief['target']['id'] == target['session_id']
    assert 'secret_hash' not in str(context)
    assert owner['session_key'] not in str(context)


def test_owner_and_claim_remain_until_actual_acceptance_and_record_updates(desk):
    owner = register(desk, 'Owner')
    target = register(desk, 'Target', 'claude')
    other = register(desk, 'Other')
    task = owned_task(desk, owner)
    offered = prepare(desk, owner, task, target)

    with pytest.raises(PermissionError):
        desk.handoff(other['session_key'], task['id'], offered['task']['version'], '', accept=True)
    still_owned = desk.get_task_context(owner['session_key'], task['id'])['task']
    assert still_owned['owner'] == owner['session_id']
    assert still_owned['pending_owner'] == target['session_id']

    accepted = desk.handoff(target['session_key'], task['id'], offered['task']['version'], '', accept=True)
    assert accepted['owner'] == target['session_id']
    assert accepted['pending_owner'] is None
    context = desk.get_task_context(target['session_key'], task['id'])
    brief = context['latest_handoff']
    assert brief['status'] == 'accepted'
    assert brief['accepted_by'] == target['session_id']
    assert brief['brief'].startswith('Progress: Implemented')


def test_superseded_and_reassigned_briefs_cannot_be_marked_accepted(desk):
    owner = register(desk, 'Owner')
    target = register(desk, 'Target', 'claude')
    replacement = register(desk, 'Replacement')
    task = owned_task(desk, owner)
    first = prepare(desk, owner, task, target)
    second = prepare(desk, owner, first['task'], target)
    accepted = desk.handoff(target['session_key'], task['id'], second['task']['version'], '', accept=True)
    assert accepted['owner'] == target['session_id']
    briefs = desk.get_task_context(target['session_key'], task['id'])['handoff_briefs']
    by_id = {brief['id']: brief for brief in briefs}
    assert by_id[first['handoff_id']]['status'] == 'superseded'
    assert by_id[second['handoff_id']]['status'] == 'accepted'

    task = owned_task(desk, replacement, 'src/reassigned')
    old = prepare(desk, replacement, task, target)
    reassigned = desk.human('media-intelligence', 'reassign', {
        'task_id':task['id'], 'version':old['task']['version'],
        'session_id':owner['session_id'], 'reason':'the owner reassigned this work'})
    with pytest.raises(PermissionError):
        desk.handoff(target['session_key'], task['id'], reassigned['version'], '', accept=True)
    brief = desk.get_task_context(owner['session_key'], task['id'])['latest_handoff']
    assert brief['id'] == old['handoff_id']
    assert brief['status'] == 'reassigned'


def test_task_change_invalidates_prepared_brief_before_acceptance(desk):
    owner = register(desk, 'Owner')
    target = register(desk, 'Target', 'claude')
    task = owned_task(desk, owner)
    prepared = prepare(desk, owner, task, target)
    changed = desk.update(owner['session_key'], task['id'], prepared['task']['version'],
                          'RUNNING', 'Continue after changing the task')
    with pytest.raises(PermissionError):
        desk.handoff(target['session_key'], task['id'], changed['version'], '', accept=True)
    brief = desk.get_task_context(target['session_key'], task['id'])['latest_handoff']
    assert brief['status'] == 'superseded'


def test_oversized_handoff_brief_rolls_back_without_partial_rows(desk):
    owner = register(desk, 'Owner')
    target = register(desk, 'Target', 'claude')
    task = owned_task(desk, owner)
    with pytest.raises(ValueError, match='too large'):
        desk.prepare_handoff(owner['session_key'], task['id'], task['version'], target['session_id'],
                             'x' * 12000, 'remaining', 'tests', 'risk', 'ref', 'branch',
                             '/tmp/owner', ['store.py'])
    current = desk.get_task_context(owner['session_key'], task['id'])['task']
    assert current['pending_owner'] is None
    assert current['version'] == task['version']
    assert desk.snapshot('media-intelligence')['messages'] == []
    with desk.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM handoff_briefs').fetchone()[0] == 0


@pytest.mark.parametrize('pause_first', [False, True])
def test_stale_or_paused_handoff_rolls_back_everything(desk, pause_first):
    owner = register(desk, 'Owner')
    target = register(desk, 'Target', 'claude')
    task = owned_task(desk, owner)
    if pause_first:
        task = desk.human('media-intelligence', 'pause', {'task_id': task['id'], 'version': task['version']})
    else:
        task = desk.update(owner['session_key'], task['id'], task['version'], 'RUNNING', 'Continue')
    before = desk.snapshot('media-intelligence')
    version = task['version'] - (0 if pause_first else 1)

    with pytest.raises(Conflict):
        prepare(desk, owner, task, target) if pause_first else desk.prepare_handoff(
            owner['session_key'], task['id'], version, target['session_id'],
            'progress', 'remaining', 'tests', 'risk', 'ref', 'branch', '/tmp/owner', ['store.py'])

    after = desk.snapshot('media-intelligence')
    current = after['tasks'][0]
    assert current['owner'] == owner['session_id']
    assert current['pending_owner'] is None
    assert current['version'] == task['version']
    assert not any(m['task_id'] == task['id'] for m in after['messages'])
    assert len(after['events']) == len(before['events'])
    with desk.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM handoff_briefs').fetchone()[0] == 0


def test_context_rejects_wrong_project(desk):
    owner = register(desk, 'Owner', project='one')
    outsider = register(desk, 'Outsider', project='two')
    task = owned_task(desk, owner)
    with pytest.raises(PermissionError):
        desk.get_task_context(outsider['session_key'], task['id'])


def test_changelog_broadcast_has_independent_receipts(desk):
    author = register(desk, 'Author')
    claude = register(desk, 'Claude', 'claude')
    peer = register(desk, 'Peer')
    task = owned_task(desk, author)
    result = desk.publish_update(author['session_key'], 'Context API shipped',
                                 'The handoff context is now available.', 'abc123',
                                 'Focused tests passed', task['id'])
    assert result['note_id'].startswith('n-')
    assert result['message_id'].startswith('m-')
    board = desk.snapshot('media-intelligence')
    note = next(n for n in board['notes'] if n['id'] == result['note_id'])
    assert note['kind'] == 'changelog'
    assert 'Context API shipped' in note['body']
    for session in (claude, peer):
        inbox = desk.check_in(session['session_key'])['inbox']
        assert any(m['id'] == result['message_id'] and m['task_id'] == task['id'] for m in inbox)
    desk.acknowledge(claude['session_key'], result['message_id'])
    assert not any(m['id'] == result['message_id'] for m in desk.check_in(claude['session_key'])['inbox'])
    assert any(m['id'] == result['message_id'] for m in desk.check_in(peer['session_key'])['inbox'])
    receipts = next(m for m in desk.snapshot('media-intelligence')['messages']
                    if m['id'] == result['message_id'])['acknowledgments']
    assert [r['session_id'] for r in receipts] == [claude['session_id']]

    dashboard = desk.human('media-intelligence', 'publish_update', {
        'title':'Dashboard update', 'body':'the owner recorded the rollout.',
        'commit_ref':'abc123', 'validation':'Dashboard action exercised', 'task_id':task['id']})
    dashboard_note = next(n for n in desk.snapshot('media-intelligence')['notes']
                          if n['id'] == dashboard['note_id'])
    assert dashboard_note['author'] == 'rohan'
    assert desk.snapshot('media-intelligence')['messages'][0]['id'] == dashboard['message_id']
