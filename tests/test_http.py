from starlette.testclient import TestClient
from server import create_app

def test_dashboard_api_and_origin_protection(tmp_path):
    app=create_app(tmp_path/'db.sqlite3',tmp_path/'ROSTER.md')
    with TestClient(app) as client:
        assert client.get('/health').json()['status']=='ok'
        assert client.get('/').status_code==200
        assert client.get('/api/state',headers={'Host':'evil.example'}).status_code==403
        payload={'action':'create','data':{'title':'Test','resources':['src/test'],'next_step':'Verify'}}
        assert client.post('/api/action',json=payload).status_code==403
        assert client.post('/api/action',json=payload,headers={'X-Project-Desk':'dashboard','Origin':'https://evil.example'}).status_code==403
        r=client.post('/api/action',json=payload,headers={'X-Project-Desk':'dashboard'})
        assert r.status_code==200
        assert r.json()['status']=='QUEUED'
        assert 'Test' in (tmp_path/'ROSTER.md').read_text()
        assert client.get('/static/app.js').status_code==200


def test_mcp_notification_binding_uses_authenticated_agent(tmp_path, monkeypatch):
    import server
    import json
    monkeypatch.setattr(server, 'STATE_ROOT', tmp_path / 'bindings/codex')
    app=create_app(tmp_path/'db.sqlite3',tmp_path/'ROSTER.md')
    registered=app.state.store.register('Claude binding test','claude','media-intelligence','test','/tmp/claude')
    thread='33333333-3333-3333-3333-333333333333'
    with TestClient(app) as client:
        response=client.post('/mcp',headers={'Accept':'application/json, text/event-stream'},json={
            'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'enable_notifications',
            'arguments':{'session_key':registered['session_key'],'agent_session_id':thread}}})
        assert response.status_code==200
        result=response.json()['result']
        assert not result.get('isError')
        value=result.get('structuredContent') or json.loads(result['content'][0]['text'])
        assert value['agent']=='claude'
        assert value['status']=='bound'
        assert registered['session_key'] not in response.text
        binding=tmp_path/'bindings/claude'/f'{thread}.json'
        assert json.loads(binding.read_text())['session_id']==registered['session_id']
