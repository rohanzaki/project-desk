import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from store import Store, Conflict

ROOT = __import__('pathlib').Path(__file__).resolve().parents[1]
PREFIX = '/srv/repos/Shop-App'
PREFIXES = [PREFIX, PREFIX + '.worktrees']


def seeded(tmp_path):
    d = Store(tmp_path / 'd.sqlite3')
    old = d.register('old shop', 'claude', 'default', 'main', PREFIX + '.worktrees/platform')
    live = d.register('live shop', 'claude', 'default', 'main', PREFIX)
    other = d.register('unrelated', 'codex', 'default', 'main', '/srv/repos/elsewhere')
    task = d.claim(old['session_key'], 'Old shop task', ['apps/web'], 'x')
    d.update(old['session_key'], task['id'], task['version'], 'DONE', 'x', 'summary', 'tests passed')
    d.message(old['session_key'], 'all', 'old news', task['id'])
    d.note(old['session_key'], 'old finding')
    stale = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    with d.connection(True) as c:
        c.execute('UPDATE sessions SET last_seen=? WHERE id IN (?,?)', (stale, old['session_id'], other['session_id']))
    return d, old, live, other, task


def test_dry_run_changes_nothing(tmp_path):
    d, old, live, other, task = seeded(tmp_path)
    report = d.move_project('default', 'shop-app', PREFIXES)
    assert report['apply'] is False
    assert [s['id'] for s in report['sessions']] == [old['session_id']]
    assert [s['id'] for s in report['skipped_live']] == [live['session_id']]
    assert [t['id'] for t in report['tasks']] == [task['id']]
    assert (report['messages'], report['notes'], report['handoff_briefs']) == (1, 1, 0)
    assert d.snapshot('shop-app')['tasks'] == []


def test_apply_moves_and_leaves_a_note(tmp_path):
    d, old, live, other, task = seeded(tmp_path)
    d.move_project('default', 'shop-app', PREFIXES, apply=True, export_path='/x/export.md')
    board = d.snapshot('shop-app')
    assert [t['id'] for t in board['tasks']] == [task['id']]
    assert {s['id'] for s in board['sessions']} == {old['session_id']}
    assert any('/x/export.md' in n['body'] for n in board['notes'])
    rest = d.snapshot('default')
    assert {s['id'] for s in rest['sessions']} == {live['session_id'], other['session_id']}
    assert rest['tasks'] == []


def test_open_claims_that_would_collide_block_the_move(tmp_path):
    d = Store(tmp_path / 'd.sqlite3')
    mover = d.register('mover', 'claude', 'default', 'main', PREFIX)
    d.claim(mover['session_key'], 'Open', ['apps/web'], 'x')
    holder = d.register('holder', 'claude', 'shop-app', 'main', '/tmp/pl')
    d.claim(holder['session_key'], 'Held', ['apps'], 'x')
    stale = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    with d.connection(True) as c:
        c.execute('UPDATE sessions SET last_seen=? WHERE id=?', (stale, mover['session_id']))
    with pytest.raises(Conflict):
        d.move_project('default', 'shop-app', PREFIX, apply=True)


def test_bad_arguments(tmp_path):
    d = Store(tmp_path / 'd.sqlite3')
    with pytest.raises(ValueError):
        d.move_project('default', 'default', PREFIX)
    with pytest.raises(ValueError):
        d.move_project('default', 'shop-app', 'relative/prefix')
    with pytest.raises(ValueError):
        d.move_project('default', 'shop-app', [])


def test_sibling_repo_is_not_matched_by_prefix(tmp_path):
    d, old, live, other, task = seeded(tmp_path)
    sibling = d.register('sibling', 'claude', 'default', 'main', '/srv/repos/Shop-App-Reports/checkout')
    stale = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    with d.connection(True) as c:
        c.execute('UPDATE sessions SET last_seen=? WHERE id=?', (stale, sibling['session_id']))
    report = d.move_project('default', 'shop-app', PREFIXES)
    touched = {s['id'] for s in report['sessions']} | {s['id'] for s in report['skipped_live']}
    assert sibling['session_id'] not in touched


def test_trailing_slash_prefix_matches_same_as_without(tmp_path):
    d, old, live, other, task = seeded(tmp_path)
    bare = d.move_project('default', 'shop-app', PREFIX)
    slashed = d.move_project('default', 'shop-app', PREFIX + '/')
    assert [s['id'] for s in slashed['sessions']] == [s['id'] for s in bare['sessions']]
    assert [s['id'] for s in slashed['skipped_live']] == [s['id'] for s in bare['skipped_live']]
    assert [t['id'] for t in slashed['tasks']] == [t['id'] for t in bare['tasks']]


def test_cli_audit_and_set_project(tmp_path):
    db = tmp_path / 'd.sqlite3'
    Store(db).create_project('shop-app', 'Shop App', [])
    run = lambda *args: subprocess.run([sys.executable, str(ROOT / 'scripts/projects_admin.py'), '--db', str(db), *args],
                                       capture_output=True, text=True, check=True).stdout
    assert json.loads(run('audit')) == []
    updated = json.loads(run('set-project', 'shop-app', '--repo-root', '/srv/repos/Shop-App'))
    assert updated['repo_roots'] == ['/srv/repos/Shop-App']


def test_cli_move_accepts_repeated_worktree_prefix(tmp_path):
    d, old, live, other, task = seeded(tmp_path)
    db = tmp_path / 'd.sqlite3'
    Store(db).create_project('shop-app', 'Shop App', [])
    out = subprocess.run([sys.executable, str(ROOT / 'scripts/projects_admin.py'), '--db', str(db),
                          'move', '--from', 'default', '--to', 'shop-app',
                          '--worktree-prefix', PREFIX, '--worktree-prefix', PREFIX + '.worktrees'],
                         capture_output=True, text=True, check=True).stdout
    report = json.loads(out)
    assert report['apply'] is False
    assert [s['id'] for s in report['sessions']] == [old['session_id']]
