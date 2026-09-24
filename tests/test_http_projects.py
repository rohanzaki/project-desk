import io
import zipfile

from starlette.testclient import TestClient

import server
from server import create_app

H = {'X-Project-Desk': 'dashboard'}


def app_for(tmp_path):
    return create_app(tmp_path / 'db.sqlite3', tmp_path / 'ROSTER.md')


def create(client, slug='xyz-app', name='XYZ App', roots=('/srv/repos/xyz',)):
    return client.post('/api/action', headers=H, json={'action': 'project.create',
                       'data': {'slug': slug, 'name': name, 'repo_roots': list(roots)}})


def test_create_list_update_projects(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        assert client.post('/api/action', json={'action': 'project.create', 'data': {}}).status_code == 403
        assert create(client).status_code == 200
        assert create(client).status_code == 409
        assert create(client, slug='Bad Slug').status_code == 400
        slugs = [p['slug'] for p in client.get('/api/projects').json()['projects']]
        assert 'xyz-app' in slugs
        updated = client.post('/api/action', headers=H, json={'action': 'project.update', 'data': {
            'slug': 'xyz-app', 'rules_path': '/r/AGENTS.md'}})
        assert updated.status_code == 200 and updated.json()['rules_path'] == '/r/AGENTS.md'


def test_pages_and_onboarding(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        create(client)
        for path in ('/p/xyz-app', '/projects', '/p/unknown-slug'):
            page = client.get(path)
            assert page.status_code == 200 and '/static/app.js' in page.text
        onboard = client.get('/p/xyz-app/onboard')
        assert onboard.status_code == 200 and onboard.headers['content-type'].startswith('text/markdown')
        assert 'http://testserver/p/xyz-app' in onboard.text
        assert client.get('/p/xyz-app/rules').status_code == 200
        kit = client.get('/p/xyz-app/kit.zip')
        assert kit.headers['content-type'] == 'application/zip'
        assert 'project-desk-xyz-app.zip' in kit.headers['content-disposition']
        assert '.project-desk.json' in zipfile.ZipFile(io.BytesIO(kit.content)).namelist()
        assert client.get('/p/xyz-app/files/.project-desk.json').json()['project'] == 'xyz-app'
        assert client.get('/p/xyz-app/files/other.txt').status_code == 404
        for path in ('/p/nope/onboard', '/p/nope/rules', '/p/nope/kit.zip', '/p/nope/files/SETUP.md',
                     '/api/projects/nope/connect'):
            assert client.get(path).status_code == 404
        connect = client.get('/api/projects/xyz-app/connect').json()
        assert connect['paste_line'] == 'Set up Project Desk for this repo from http://testserver/p/xyz-app/onboard'
        assert connect['join_command'].endswith('/desk join http://testserver/p/xyz-app')


def test_instructions_no_longer_say_use_default():
    assert 'Use project default' not in server.INSTRUCTIONS
    assert '.project-desk.json' in server.INSTRUCTIONS


def test_strict_mode_from_environment(tmp_path, monkeypatch):
    assert app_for(tmp_path).state.store.strict is False
    monkeypatch.setenv('PROJECT_DESK_STRICT', '1')
    assert create_app(tmp_path / 'b.sqlite3', tmp_path / 'R.md').state.store.strict is True


def test_desk_base_ignores_host_header_port(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        create(client)
        spoofed = {'Host': 'localhost:6666'}
        connect = client.get('/api/projects/xyz-app/connect', headers=spoofed)
        assert '6666' not in connect.text
        onboard = client.get('/p/xyz-app/onboard', headers=spoofed)
        assert '6666' not in onboard.text
        declaration = client.get('/p/xyz-app/files/.project-desk.json', headers=spoofed)
        assert '6666' not in declaration.json()['desk']


def test_roster_shows_only_its_own_project(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        client.post('/api/action', headers=H, json={'action': 'create', 'data': {
            'title': 'Roster project task', 'resources': ['src/a'], 'next_step': 'Go'}})
        client.post('/api/action', headers=H, json={'project': 'other-project', 'action': 'create', 'data': {
            'title': 'Other project task', 'resources': ['src/b'], 'next_step': 'Go'}})
        roster = (tmp_path / 'ROSTER.md').read_text()
        assert 'Roster project task' in roster and 'Other project task' not in roster
