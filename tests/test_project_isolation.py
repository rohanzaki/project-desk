"""Agents act only in their own project: one test per agent tool.

The one deliberate door is an explicit cross-project address (a session id
registered elsewhere, or '<project>:all'); crossovers are covered in
test_crossover.py. Task-level isolation below holds without a crossover."""
import json

import pytest

from store import Store, Conflict


def declared(tmp_path, slug):
    repo = tmp_path / slug
    repo.mkdir()
    (repo / '.git').mkdir()
    (repo / '.project-desk.json').write_text(json.dumps({'project': slug}))
    return str(repo)


@pytest.fixture
def two(tmp_path):
    d = Store(tmp_path / 'd.sqlite3', strict=True)
    a = d.register('alpha agent', 'codex', '', 'main', declared(tmp_path, 'alpha'))
    b = d.register('beta agent', 'claude', '', 'main', declared(tmp_path, 'beta'))
    beta_task = d.claim(b['session_key'], 'Beta work', ['src'], 'Edit')
    beta_message = d.message(b['session_key'], 'all', 'beta news')
    queued = d.human('beta', 'create', {'title': 'Queued beta', 'resources': ['docs'], 'next_step': 'Write'})
    return d, a, b, beta_task, beta_message, queued


def test_message_to_other_project_session_lands_in_that_project(two):
    d, a, b, *_ = two
    sent = d.message(a['session_key'], b['session_id'], 'hi')
    assert sent['to_project'] == 'beta'
    delivered = [m for m in d.check_in(b['session_key'], include=['inbox'])['inbox'] if m['id'] == sent['message_id']]
    assert delivered and delivered[0]['project'] == 'beta' and delivered[0]['from_project'] == 'alpha'
    assert all(m['id'] != sent['message_id'] for m in d.snapshot('alpha')['messages'])


def test_message_to_unknown_session_refused(two):
    d, a, *_ = two
    with pytest.raises(ValueError, match='Unknown recipient'):
        d.message(a['session_key'], 's-000000000000', 'hi')


def test_message_on_other_project_task_refused(two):
    d, a, b, beta_task, *_ = two
    with pytest.raises(ValueError, match='another project'):
        d.message(a['session_key'], 'all', 'hi', beta_task['id'])


def test_claiming_other_project_queued_task_refused(two):
    d, a, *_, queued = two
    with pytest.raises(Conflict, match='in your project'):
        d.claim(a['session_key'], 'x', ['docs'], 'x', queued['id'])


def test_updating_other_project_task_refused(two):
    d, a, b, beta_task, *_ = two
    with pytest.raises(PermissionError):
        d.update(a['session_key'], beta_task['id'], beta_task['version'], 'RUNNING', 'x')


def test_handoffs_across_projects_refused(two):
    d, a, b, beta_task, *_ = two
    with pytest.raises(PermissionError, match='another project'):
        d.handoff(a['session_key'], beta_task['id'], beta_task['version'], b['session_id'])
    with pytest.raises(PermissionError, match='another project'):
        d.prepare_handoff(a['session_key'], beta_task['id'], beta_task['version'], b['session_id'],
                          'p', 'r', 'v', 'k', 'c', 'b', '/tmp', ['src'])


def test_reading_other_project_task_context_refused(two):
    d, a, b, beta_task, *_ = two
    with pytest.raises(PermissionError, match='another project'):
        d.get_task_context(a['session_key'], beta_task['id'])


def test_acknowledging_other_project_message_refused(two):
    d, a, b, beta_task, beta_message, _ = two
    result = d.acknowledge(a['session_key'], message_ids=[beta_message['message_id']])
    assert result['failed'] and not result['acknowledged']


def test_publish_update_on_other_project_task_refused(two):
    d, a, b, beta_task, *_ = two
    with pytest.raises(ValueError, match='another project'):
        d.publish_update(a['session_key'], 't', 'b', 'c', 'v', beta_task['id'])


def test_notes_and_check_in_stay_in_own_project(two):
    d, a, *_ = two
    d.note(a['session_key'], 'alpha finding')
    assert [n['body'] for n in d.snapshot('alpha')['notes']] == ['alpha finding']
    assert all(n['body'] != 'alpha finding' for n in d.snapshot('beta')['notes'])
    assert d.check_in(a['session_key'], include=['board'])['board']['project'] == 'alpha'


def test_path_claims_do_not_collide_across_projects(two):
    d, a, *_ = two
    assert d.would_conflict(a['session_key'], ['src'])['clear'] is True


def test_human_can_act_in_any_project(two):
    d, *_ = two
    assert d.human('beta', 'message', {'recipient': 'all', 'body': 'from the human'})['status'] == 'sent'
