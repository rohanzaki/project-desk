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


def test_dashboard_can_reopen_and_reassign_a_completed_task(tmp_path):
    app=create_app(tmp_path/'db.sqlite3',tmp_path/'ROSTER.md')
    target=app.state.store.register('Codex reopen target','codex','media-intelligence','test/reopen','/tmp/reopen')
    with TestClient(app) as client:
        headers={'X-Project-Desk':'dashboard'}
        created=client.post('/api/action',headers=headers,json={'action':'create','data':{
            'title':'Completed workflow','resources':['src/completed'],'next_step':'Initial work'}}).json()
        completed=client.post('/api/action',headers=headers,json={'action':'close','data':{
            'task_id':created['id'],'version':created['version'],'summary':'First cycle completed'}}).json()

        response=client.post('/api/action',headers=headers,json={'action':'reopen','data':{
            'task_id':completed['id'],'version':completed['version'],
            'session_id':target['session_id'],'next_step':'Address the follow-up review'}})

        assert response.status_code==200
        reopened=response.json()
        assert reopened['status']=='RUNNING'
        assert reopened['owner']==target['session_id']
        assert reopened['next_step']=='Address the follow-up review'
        assert reopened['summary']=='First cycle completed'
        assert reopened['version']==completed['version']+1
        board=client.get('/api/state').json()
        assert any(task['id']==reopened['id'] and task['status']=='RUNNING' for task in board['tasks'])


def test_reopen_refuses_active_tasks_and_resource_conflicts(tmp_path):
    app=create_app(tmp_path/'db.sqlite3',tmp_path/'ROSTER.md')
    target=app.state.store.register('Codex reopen target','codex','media-intelligence','test/reopen','/tmp/reopen')
    owner=app.state.store.register('Claude active owner','claude','media-intelligence','test/active','/tmp/active')
    with TestClient(app) as client:
        headers={'X-Project-Desk':'dashboard'}
        created=client.post('/api/action',headers=headers,json={'action':'create','data':{
            'title':'Completed workflow','resources':['src/shared'],'next_step':'Initial work'}}).json()
        active_response=client.post('/api/action',headers=headers,json={'action':'reopen','data':{
            'task_id':created['id'],'version':created['version'],'session_id':target['session_id'],'next_step':'Wrong state'}})
        assert active_response.status_code==409

        completed=client.post('/api/action',headers=headers,json={'action':'close','data':{
            'task_id':created['id'],'version':created['version'],'summary':'First cycle completed'}}).json()
        app.state.store.claim(owner['session_key'],'Other active work',['src/shared'],'Protect the newer edit')
        conflict=client.post('/api/action',headers=headers,json={'action':'reopen','data':{
            'task_id':completed['id'],'version':completed['version'],
            'session_id':target['session_id'],'next_step':'Would overlap newer work'}})
        assert conflict.status_code==409
        state=client.get('/api/state').json()
        original=next(task for task in state['tasks'] if task['id']==completed['id'])
        assert original['status']=='DONE'
        assert original['version']==completed['version']


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
