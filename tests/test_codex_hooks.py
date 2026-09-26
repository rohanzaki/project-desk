import json
import stat
from pathlib import Path

import pytest

import codex_hooks as hooks
from store import Store

THREAD = '11111111-1111-1111-1111-111111111111'


@pytest.fixture
def desk(tmp_path):
    store = Store(tmp_path / 'desk.sqlite3')
    session = store.register('Hook test', 'codex', 'test', 'test', '/tmp/test-worktree')
    state_root = tmp_path / 'bindings'
    source = tmp_path / 'source.json'
    hooks.private_write(source, session)
    call = lambda tool, args: store.check_in(args['session_key'], args['since'])
    path = hooks.bind(THREAD, source, 'test', state_root, call)
    return store, session, state_root, path, call


def payload(event='PreToolUse', **extra):
    return {'session_id': THREAD, 'hook_event_name': event, **extra}


def run(desk, event='PreToolUse', **extra):
    return hooks.run_hook(payload(event, **extra), desk[2], desk[4])


def test_binding_private_idempotent_and_rejects_rebinding(desk, tmp_path):
    store, session, root, path, call = desk
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    other = store.register('Other', 'codex', 'test', 'test', '/tmp/other')
    source = tmp_path / 'other.json'
    hooks.private_write(source, other)
    with pytest.raises(ValueError, match='different session'):
        hooks.bind(THREAD, source, 'test', root, call)
    source.chmod(0o644)
    with pytest.raises(ValueError, match='private regular'):
        hooks.bind(THREAD, source, 'test', root, call)


def test_unbound_thread_no_network(tmp_path):
    def unexpected(*args):
        pytest.fail('Unenrolled thread must not contact Project Desk')
    assert hooks.run_hook(payload(), tmp_path, unexpected) == {}
    with pytest.raises(ValueError):
        hooks.binding_path(tmp_path, '../../another-session')


def test_detects_reassignment_to_other_identity(desk):
    store, session, root, path, call = desk
    task = store.claim(session['session_key'], 'Owned work', ['src/hooks'], 'check tests')
    assert 'YOUR TASK' in json.dumps(run(desk, 'SessionStart'))
    assert run(desk) == {}
    peer = store.register('Different Codex', 'codex', 'test', 'peer', '/tmp/peer')
    store.human('test', 'reassign', {'task_id': task['id'], 'version': task['version'],
                                   'session_id': peer['session_id'], 'reason': 'move'})
    result = json.dumps(run(desk))
    assert peer['session_id'] in result
    assert 'Different Codex' in result
    assert 'task.reassign' in result


def test_message_notification_is_not_acknowledgment(desk):
    store, session, root, path, call = desk
    message = store.human('test', 'message', {'recipient': session['session_id'], 'body': 'Please check ownership'})
    first = run(desk)
    assert message['message_id'] in json.dumps(first)
    assert run(desk) == {}
    assert len(store.check_in(session['session_key'])['inbox']) == 1
    assert message['message_id'] in json.dumps(run(desk, 'UserPromptSubmit'))


def test_pages_events_and_retains_human_decisions(desk):
    store, session, root, path, call = desk
    for i in range(105):
        store.note(session['session_key'], f'noise {i}')
    note = store.human('test', 'note', {'body': 'Preserve existing branding'})
    result = json.dumps(run(desk))
    assert note['note_id'] in result
    assert 'Preserve existing branding' in result
    assert hooks.private_read(path)['cursor'] == store.check_in(session['session_key'], 100)['cursor']
    assert run(desk) == {}


def test_stop_continues_once_only_for_new_update(desk):
    store, session, root, path, call = desk
    store.human('test', 'message', {'recipient': session['session_id'], 'body': 'Review ownership'})
    assert run(desk, 'Stop')['decision'] == 'block'
    assert run(desk, 'Stop') == {}
    store.human('test', 'message', {'recipient': session['session_id'], 'body': 'Another update'})
    before = path.read_text()
    assert run(desk, 'Stop', stop_hook_active=True) == {}
    assert path.read_text() == before  # pending notification isn't lost
    assert 'Another update' in json.dumps(run(desk, 'UserPromptSubmit'))


def test_human_pause_never_triggers_stop_continuation(desk):
    store, session, root, path, call = desk
    task = store.claim(session['session_key'], 'Owned work', ['src/hooks'], 'check tests')
    run(desk)
    store.human('test', 'pause', {'task_id': task['id'], 'version': task['version']})
    result = run(desk, 'Stop')
    assert 'decision' not in result
    assert 'PAUSED' in result['systemMessage']
    assert 'PAUSED' in json.dumps(run(desk, 'UserPromptSubmit'))
    assert store.check_in(session['session_key'])['board']['tasks'][0]['human_paused'] == 1


def test_outage_preserves_cursor_and_does_not_continue(desk):
    store, session, root, path, call = desk
    before = hooks.private_read(path)
    def offline(*args):
        raise RuntimeError(session['session_key'])
    result = hooks.run_hook(payload('Stop'), root, offline)
    assert 'decision' not in result
    assert session['session_key'] not in json.dumps(result)
    assert hooks.private_read(path)['cursor'] == before['cursor']
    assert hooks.run_hook(payload(), root, offline) == {}


def test_failed_second_page_does_not_lose_first_page(desk):
    store, session, root, path, call = desk
    for i in range(105):
        store.note(session['session_key'], f'noise {i}')
    before = hooks.private_read(path)['cursor']
    calls = []
    def fail_second(tool, args):
        calls.append(args['since'])
        if len(calls) == 2:
            raise RuntimeError('network')
        return call(tool, args)
    hooks.run_hook(payload(), root, fail_second)
    assert len(calls) == 2
    assert hooks.private_read(path)['cursor'] == before


def test_context_bounded_and_never_prints_session_key(desk):
    store, session, root, path, call = desk
    for i in range(30):
        store.human('test', 'message', {'recipient': session['session_id'],
                    'body': session['session_key'] + (' content' * 200)})
    result = json.dumps(run(desk))
    assert session['session_key'] not in result
    assert len(result) < 7800
    assert 'Truncated' in result


def test_posttool_throttle_and_pretool_freshness(desk):
    store, session, root, path, call = desk
    hooks.run_hook(payload(), root, call, clock=lambda: 1000)
    store.human('test', 'message', {'recipient': session['session_id'], 'body': 'urgent'})
    assert hooks.run_hook(payload('PostToolUse'), root, call, clock=lambda: 1001) == {}
    assert 'urgent' in json.dumps(hooks.run_hook(payload(), root, call, clock=lambda: 1001))


def test_install_preserves_existing_hooks_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(hooks, 'ROOT', tmp_path)
    file = tmp_path / 'hooks.json'
    original = {'SessionStart': [{'hooks': [{'type': 'command', 'command': 'existing-gsd-hook'}]}]}
    file.write_text(json.dumps(original))
    hooks.install(file)
    first = json.loads(file.read_text())
    assert first['hooks']['SessionStart'][0] == original['SessionStart'][0]
    assert set(hooks.EVENTS) <= set(first['hooks'])
    assert 'SessionStart' not in first
    hooks.install(file)
    assert json.loads(file.read_text()) == first
    assert len(list((tmp_path / 'data/hook-backups').glob('*.json'))) == 1


def test_identity_mismatch_never_advances_cursor(desk):
    store, session, root, path, call = desk
    def wrong_identity(tool, args):
        response = call(tool, args)
        response['session_id'] = 'another-session'
        return response
    start = hooks.private_read(path)['cursor']
    result = hooks.run_hook(payload(), root, wrong_identity)
    assert 'unavailable' in json.dumps(result)
    assert hooks.private_read(path)['cursor'] == start


def test_own_status_updates_do_not_generate_stop_loops(desk):
    store, session, root, path, call = desk
    task = store.claim(session['session_key'], 'Owned work', ['src/hooks'], 'check tests')
    assert run(desk, 'Stop') == {}
    store.update(session['session_key'], task['id'], task['version'], 'RUNNING', 'tests passed')
    assert run(desk, 'Stop') == {}


def test_inbox_overflow_reports_undelivered_message_ids(desk):
    store, session, root, path, call = desk
    for i in range(101):
        last = store.human('test', 'message', {'recipient': session['session_id'], 'body': f'Update {i}'})
    # Inspect collector output before the overall output cap.
    state = hooks.private_read(path)
    first = store.check_in(session['session_key'], 0)
    hooks.collect(state, first)
    second = store.check_in(session['session_key'], state['cursor'])
    lines, _ = hooks.collect(state, second)
    assert any(last['message_id'] in line and 'body has not been delivered' in line for line in lines)


def test_concurrent_hooks_deliver_single_notification(desk):
    from concurrent.futures import ThreadPoolExecutor
    store, session, root, path, call = desk
    store.human('test', 'message', {'recipient': session['session_id'], 'body': 'Only once'})
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: run(desk), range(3)))
    assert sum(bool(result) for result in results) == 1


def test_broadcast_reaches_both_bound_agents_with_independent_receipts(desk, tmp_path):
    store, codex, codex_root, path, call = desk
    claude = store.register('Claude hook', 'claude', 'test', 'test', '/tmp/claude')
    thread = '22222222-2222-2222-2222-222222222222'
    claude_root = tmp_path / 'claude'
    hooks.bind_credentials(thread, claude, 'test', claude_root, call, 'claude')
    message = store.human('test', 'message', {'recipient': 'all', 'body': 'Both agents: check task discussion'})
    assert message['message_id'] in json.dumps(run(desk))
    other = hooks.run_hook({**payload(), 'session_id': thread}, claude_root, call)
    assert message['message_id'] in json.dumps(other)
    assert not store.snapshot('test')['messages'][0]['acknowledgments']
    store.acknowledge(codex['session_key'], message['message_id'])
    assert store.check_in(claude['session_key'])['inbox'][0]['id'] == message['message_id']
    store.acknowledge(claude['session_key'], message['message_id'])
    assert len(store.snapshot('test')['messages'][0]['acknowledgments']) == 2


def test_wrong_agent_kind_cannot_bind(desk, tmp_path):
    store, session, root, path, call = desk
    with pytest.raises(ValueError, match='agent kind'):
        hooks.bind_credentials(THREAD, session, 'test', tmp_path / 'claude', call, 'claude')


def test_claude_install_preserves_settings(desk, tmp_path, monkeypatch):
    monkeypatch.setattr(hooks, 'ROOT', tmp_path)
    file = tmp_path / 'settings.json'
    original = {'model': 'existing-model', 'permissions': {'deny': ['existing-rule']},
                'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'old-stop'}]}]}}
    file.write_text(json.dumps(original))
    hooks.install(file, 'claude')
    installed = json.loads(file.read_text())
    assert installed['model'] == original['model']
    assert installed['permissions'] == original['permissions']
    assert installed['hooks']['Stop'][0] == original['hooks']['Stop'][0]
    assert '--agent claude' in installed['hooks']['Stop'][1]['hooks'][0]['command']


def test_other_agents_completed_work_and_notes_are_visible(desk):
    store, session, root, path, call = desk
    run(desk)
    other = store.register('Peer worker', 'claude', 'test', 'peer', '/tmp/peer')
    task = store.claim(other['session_key'], 'Separate task', ['src/other'], 'Verify it')
    store.update(other['session_key'], task['id'], task['version'], 'DONE', '',
                 'Fixed the shared contract', 'Focused tests passed')
    note = store.note(other['session_key'], 'Consumers should use the updated contract')
    result = json.dumps(run(desk))
    assert 'Fixed the shared contract' in result
    assert note['note_id'] in result
    assert run(desk) == {}


def test_incoming_messages_have_priority_over_busy_task_board(desk):
    store, session, root, path, call = desk
    other = store.register('Peer worker', 'claude', 'test', 'peer', '/tmp/peer')
    for i in range(30):
        store.claim(other['session_key'], f'Other task {i}', [f'src/other/{i}'], 'x' * 500)
    store.human('test', 'message', {'recipient': 'all', 'body': 'Important new instruction'})
    result = json.dumps(run(desk))
    assert 'Important new instruction' in result
    assert result.index('Important new instruction') < result.index('OTHER CLAIM')
