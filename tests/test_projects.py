import json
import subprocess

import pytest

import projects


def git(*args, cwd):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)


def make_repo(path, declaration=None):
    path.mkdir(parents=True)
    git('init', '-q', '-b', 'main', cwd=path)
    git('config', 'user.email', 'test@example.com', cwd=path)
    git('config', 'user.name', 'Test', cwd=path)
    if declaration is not None:
        (path / '.project-desk.json').write_text(json.dumps(declaration))
    (path / 'README.md').write_text('x\n')
    git('add', '-A', cwd=path)
    git('commit', '-q', '-m', 'init', cwd=path)
    return path


def test_file_in_worktree(tmp_path):
    repo = make_repo(tmp_path / 'app', {'project': 'shop-app', 'name': 'Shop App',
                                        'desk': 'http://127.0.0.1:7331'})
    (repo / 'src' / 'deep').mkdir(parents=True)
    found = projects.resolve_project(repo / 'src' / 'deep')
    assert (found.project, found.source, found.name, found.desk) == (
        'shop-app', 'file', 'Shop App', 'http://127.0.0.1:7331')
    assert found.root == str(repo.resolve())


def test_outside_worktree_without_the_file_uses_git_main(tmp_path):
    repo = make_repo(tmp_path / 'app')                      # committed without the file
    (tmp_path / 'app.worktrees').mkdir()
    git('worktree', 'add', '-q', str(tmp_path / 'app.worktrees' / 'feature'), '-b', 'feature', cwd=repo)
    (repo / '.project-desk.json').write_text(json.dumps({'project': 'shop-app'}))
    found = projects.resolve_project(tmp_path / 'app.worktrees' / 'feature')
    assert (found.project, found.source) == ('shop-app', 'git-main')
    assert found.root == str(repo.resolve())


def test_walk_stops_at_the_repo_root(tmp_path):
    (tmp_path / '.project-desk.json').write_text(json.dumps({'project': 'outer'}))
    repo = make_repo(tmp_path / 'inner')
    assert projects.resolve_project(repo) is None


def test_registry_fallback_matches_path_and_git_main(tmp_path):
    repo = make_repo(tmp_path / 'clone')
    git('worktree', 'add', '-q', str(tmp_path / 'wt'), '-b', 'x', cwd=repo)
    registry = {'media-intelligence': [str(repo)]}
    assert projects.resolve_project(repo / 'not-created-yet', registry).project == 'media-intelligence'
    found = projects.resolve_project(tmp_path / 'wt', registry)
    assert (found.project, found.source) == ('media-intelligence', 'registry')


def test_file_beats_registry(tmp_path):
    repo = make_repo(tmp_path / 'app', {'project': 'shop-app'})
    found = projects.resolve_project(repo, {'media-intelligence': [str(repo)]})
    assert found.project == 'shop-app'


@pytest.mark.parametrize('content', ['{not json', json.dumps({'project': 'Bad Slug'}),
                                     json.dumps(['x']), json.dumps({'project': 'ok', 'pad': 'x' * 5000})])
def test_malformed_declaration_counts_as_absent(tmp_path, content):
    repo = make_repo(tmp_path / 'app')
    (repo / '.project-desk.json').write_text(content)
    assert projects.resolve_project(repo) is None


def test_missing_and_plain_paths(tmp_path):
    assert projects.resolve_project(tmp_path / 'does-not-exist') is None
    plain = tmp_path / 'plain'
    plain.mkdir()
    assert projects.resolve_project(plain) is None


def test_git_timeout_counts_as_no_project(tmp_path, monkeypatch):
    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired('git', 2)
    monkeypatch.setattr(projects.subprocess, 'run', slow)
    assert projects.resolve_project(tmp_path) is None


def test_slug_and_display_name():
    assert projects.valid_slug('shop-app') and not projects.valid_slug('Shop')
    assert not projects.valid_slug('-x') and not projects.valid_slug('')
    assert projects.display_name('media-intelligence') == 'Media Intelligence'
    assert projects.display_name('default') == 'Unsorted'
