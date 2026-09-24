import pytest

from store import Store, Conflict


def test_backfill_names_existing_projects(tmp_path):
    db = tmp_path / 'd.sqlite3'
    first = Store(db)
    first.register('a', 'codex', 'shop-app', 'b', '/tmp/x')
    first.register('b', 'claude', 'default', 'b', '/tmp/y')
    with first.connection(True) as c:
        c.execute('DELETE FROM projects')          # as if the database predates the table
    items = {p['slug']: p for p in Store(db).list_projects()}
    assert items['shop-app']['name'] == 'Shop App'
    assert items['shop-app']['hidden'] is False
    assert items['default']['name'] == 'Unsorted'
    assert items['default']['hidden'] is True          # no open work


def test_list_projects_counts(tmp_path):
    d = Store(tmp_path / 'd.sqlite3')
    a = d.register('a', 'codex', 'shop-app', 'b', '/tmp/x')
    d.claim(a['session_key'], 'Work', ['src'], 'Do it')
    sent = d.message(a['session_key'], 'human', 'Please look')
    item = next(p for p in d.list_projects() if p['slug'] == 'shop-app')
    assert (item['open_tasks'], item['unread_human'], item['live_sessions']) == (1, 1, 1)
    assert item['last_activity']
    d.human('shop-app', 'ack', {'message_id': sent['message_id']})
    item = next(p for p in d.list_projects() if p['slug'] == 'shop-app')
    assert item['unread_human'] == 0


def test_create_project_validates(tmp_path):
    d = Store(tmp_path / 'd.sqlite3')
    made = d.create_project('xyz-app', 'XYZ App', ['/srv/repos/xyz/'])
    assert made['repo_roots'] == ['/srv/repos/xyz'] and made['name'] == 'XYZ App'
    assert d.project('xyz-app')['slug'] == 'xyz-app'
    with pytest.raises(Conflict):
        d.create_project('xyz-app', 'Again', [])
    for bad in ('XYZ', '-x', 'a b', ''):
        with pytest.raises(ValueError):
            d.create_project(bad, 'Name', [])
    with pytest.raises(ValueError):
        d.create_project('ok', 'Name', ['relative/path'])
    with pytest.raises(ValueError):
        d.project('nope')


def test_update_project(tmp_path):
    d = Store(tmp_path / 'd.sqlite3')
    d.create_project('xyz', 'XYZ', [])
    updated = d.update_project('xyz', repo_roots=['/a', '/b'], rules_path='/r/AGENTS.md', archived=True)
    assert updated['repo_roots'] == ['/a', '/b'] and updated['rules_path'] == '/r/AGENTS.md'
    assert updated['archived'] is True
    with d.connection() as c:
        assert 'xyz' not in d.registry(c)          # archived projects are not used for resolution
    with pytest.raises(ValueError):
        d.update_project('nope', name='x')
    with pytest.raises(ValueError):
        d.update_project('xyz', rules_path='relative.md')


def test_snapshot_shows_other_projects_service_locks(tmp_path):
    d = Store(tmp_path / 'd.sqlite3')
    a = d.register('a', 'codex', 'media-intelligence', 'b', '/tmp/x')
    b = d.register('b', 'claude', 'shop-app', 'b', '/tmp/y')
    d.claim(a['session_key'], 'Deploy', ['service:gpu-box'], 'Ship')
    d.claim(b['session_key'], 'Local', ['src'], 'Edit')
    locks = d.snapshot('shop-app')['shared_locks']
    assert [l['resource'] for l in locks] == ['service:gpu-box']
    assert locks[0]['project'] == 'media-intelligence'
    assert d.snapshot('media-intelligence')['shared_locks'] == []
