import json

import pytest

from store import Store


def declared(tmp_path, slug, folder=None):
    repo = tmp_path / (folder or slug)
    repo.mkdir()
    (repo / '.git').mkdir()                                  # marks the repo root for the walk
    (repo / '.project-desk.json').write_text(json.dumps({'project': slug}))
    return str(repo)


def test_omitted_project_resolves_from_the_worktree(tmp_path):
    d = Store(tmp_path / 'd.sqlite3')
    got = d.register('a', 'claude', '', 'main', declared(tmp_path, 'shop-app'))
    assert got['project'] == 'shop-app' and 'hint' not in got
    assert d.check_in(got['session_key'], include=['counts'])['session_id'] == got['session_id']
    assert any(p['slug'] == 'shop-app' for p in d.list_projects())


def test_matching_project_is_accepted(tmp_path):
    d = Store(tmp_path / 'd.sqlite3')
    assert d.register('a', 'codex', 'shop-app', 'main', declared(tmp_path, 'shop-app'))['project'] == 'shop-app'


def test_wrong_project_is_refused(tmp_path):
    d = Store(tmp_path / 'd.sqlite3')
    worktree = declared(tmp_path, 'shop-app')
    for wrong in ('default', 'media-intelligence'):
        with pytest.raises(ValueError, match='belongs to project shop-app'):
            d.register('a', 'codex', wrong, 'main', worktree)


def test_undeclared_worktree_keeps_todays_behaviour(tmp_path):
    d = Store(tmp_path / 'd.sqlite3')
    explicit = d.register('a', 'codex', 'media-intelligence', 'main', '/tmp/undeclared-worktree')
    assert explicit['project'] == 'media-intelligence' and 'projects' in explicit['hint']
    assert d.register('b', 'codex', '', 'main', '/tmp/undeclared-worktree')['project'] == 'default'
    custom = Store(tmp_path / 'e.sqlite3', default_project='media-intelligence')
    assert custom.register('c', 'codex', None, 'main', '/tmp/undeclared-worktree')['project'] == 'media-intelligence'


def test_strict_mode(tmp_path):
    d = Store(tmp_path / 'd.sqlite3', strict=True, desk_url='http://127.0.0.1:7399')
    with pytest.raises(ValueError, match='http://127.0.0.1:7399/projects'):
        d.register('a', 'codex', 'media-intelligence', 'main', '/tmp/undeclared-worktree')
    with pytest.raises(ValueError, match='belongs to project alpha'):
        d.register('a', 'codex', 'beta', 'main', declared(tmp_path, 'alpha'))
    assert d.register('a', 'codex', '', 'main', str(tmp_path / 'alpha'))['project'] == 'alpha'


def test_registry_root_counts_as_a_declaration_under_strict(tmp_path):
    d = Store(tmp_path / 'd.sqlite3', strict=True)
    d.create_project('media-intelligence', 'Media Intelligence', [str(tmp_path / 'clone')])
    (tmp_path / 'clone').mkdir()
    assert d.register('a', 'claude', '', 'main', str(tmp_path / 'clone'))['project'] == 'media-intelligence'


def test_sessions_from_before_strict_keep_working(tmp_path):
    db = tmp_path / 'd.sqlite3'
    old = Store(db).register('a', 'codex', 'media-intelligence', 'main', '/tmp/undeclared-worktree')
    strict = Store(db, strict=True)
    assert strict.check_in(old['session_key'], include=['counts'])['session_id'] == old['session_id']
    task = strict.claim(old['session_key'], 'Keep going', ['src'], 'Edit')
    assert strict.update(old['session_key'], task['id'], task['version'], 'RUNNING', 'Still editing')['version'] == 2


def test_mismatch_report(tmp_path):
    d = Store(tmp_path / 'd.sqlite3')
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    s = d.register('a', 'codex', 'default', 'main', str(repo))           # undeclared at registration
    (repo / '.project-desk.json').write_text(json.dumps({'project': 'shop-app'}))
    report = d.session_mismatches(hours=6)
    assert [(r['id'], r['project'], r['declared']) for r in report] == [(s['session_id'], 'default', 'shop-app')]
