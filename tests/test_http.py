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
