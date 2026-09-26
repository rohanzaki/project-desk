"""Which page the desk opens with, and the HTTP surface of desk feature requests."""
import json

from starlette.testclient import TestClient

from server import create_app

H = {'X-Project-Desk': 'dashboard'}


def declared(tmp_path, slug):
    repo = tmp_path / slug
    repo.mkdir(exist_ok=True)
    (repo / '.git').mkdir(exist_ok=True)
    (repo / '.project-desk.json').write_text(json.dumps({'project': slug}))
    return str(repo)


def app_for(tmp_path, monkeypatch):
    monkeypatch.setenv('PROJECT_DESK_STRICT', '1')
    return create_app(tmp_path / 'db.sqlite3', tmp_path / 'ROSTER.md')


def test_the_classic_page_stays_the_default_until_the_human_switches(tmp_path, monkeypatch):
    app = app_for(tmp_path, monkeypatch)
    with TestClient(app) as client:
        classic, v2 = client.get('/classic').text, client.get('/v2').text
        assert classic != v2 and 'static/v2' in v2
        assert client.get('/api/ui').json() == {'default': 'classic'}
        assert client.get('/').text == classic and client.get('/p/alpha').text == classic
        switched = client.post('/api/action', headers=H, json={'action': 'ui.default', 'data': {'page': 'v2'}})
        assert switched.status_code == 200 and switched.json() == {'default': 'v2'}
        assert client.get('/').text == v2 and client.get('/projects').text == v2
        assert client.get('/classic').text == classic   # always reachable
        bad = client.post('/api/action', headers=H, json={'action': 'ui.default', 'data': {'page': 'nope'}})
        assert bad.status_code == 400
        client.post('/api/action', headers=H, json={'action': 'ui.default', 'data': {'page': 'classic'}})
        assert client.get('/').text == classic


def test_desk_requests_over_http_and_mcp(tmp_path, monkeypatch):
    app = app_for(tmp_path, monkeypatch)
    a = app.state.store.register('alpha', 'claude', '', 'main', declared(tmp_path, 'alpha'))

    def call(client, name, arguments):
        response = client.post('/mcp', headers={'Accept': 'application/json, text/event-stream'}, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})
        result = response.json()['result']
        return result

    with TestClient(app) as client:
        made = call(client, 'request_desk_feature', {'session_key': a['session_key'], 'title': 'Slim hook board',
                                                     'why': 'hook check-ins time out'})
        assert not made.get('isError'), made
        rid = (made.get('structuredContent') or json.loads(made['content'][0]['text']))['request']['id']
        refused = call(client, 'volunteer_desk_request', {'session_key': a['session_key'], 'request_id': rid})
        assert refused.get('isError') and 'not approved' in json.dumps(refused)
        listed = client.get('/api/desk-requests?status=proposed').json()['requests']
        assert [r['id'] for r in listed] == [rid]
        items = client.get('/api/attention').json()['items']
        assert any(i['kind'] == 'desk_request' and i['request_id'] == rid for i in items)
        ok = client.post('/api/action', headers=H, json={'project': 'alpha', 'action': 'desk_request.approve',
                                                         'data': {'request_id': rid, 'note': 'small'}})
        assert ok.status_code == 200 and ok.json()['status'] == 'approved'
        assert client.get('/api/desk-requests?status=bogus').status_code == 400
        took = call(client, 'volunteer_desk_request', {'session_key': a['session_key'], 'request_id': rid})
        assert not took.get('isError'), took
        assert client.get('/api/desk-requests?status=in_progress').json()['requests'][0]['id'] == rid
