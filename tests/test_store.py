import concurrent.futures
import sqlite3
import threading
import pytest
from store import Store, Conflict, scopes

@pytest.fixture
def desk(tmp_path): return Store(tmp_path/'desk.sqlite3')

def register(desk,name='one',kind='codex',project='media-intelligence'):
    return desk.register(name,kind,project,'feat/test','/tmp/test-worktree')

def task(desk,session,resources=None):
    return desk.claim(session['session_key'],'Build capture UI',resources or ['src/capture'],'Implement and verify')

def test_atomic_claim_under_concurrent_sessions(desk):
    agents=[register(desk,str(i)) for i in range(8)]
    barrier=threading.Barrier(8)
    def run(agent):
        barrier.wait()
        try: return task(desk,agent)['id']
        except Conflict: return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(run,agents))
    assert sum(r is not None for r in results)==1

def test_directory_overlap_and_segment_boundary(desk):
    a,b=register(desk),register(desk,'two','claude')
    task(desk,a,['src/capture'])
    with pytest.raises(Conflict): task(desk,b,['src/capture/component.tsx'])
    with pytest.raises(Conflict): task(desk,b,['src'])
    assert task(desk,b,['src/capture-v2'])

def test_claims_are_project_scoped(desk):
    task(desk,register(desk))
    assert task(desk,register(desk,'other',project='other-project'))

def test_service_claim_is_exclusive(desk):
    task(desk,register(desk),['service:mintel-app-deploy'])
    with pytest.raises(Conflict): task(desk,register(desk,'two'),['service:mintel-app-deploy'])

@pytest.mark.parametrize('value',['../src','/src','src/../x','src/**','C:\\src','service:'])
def test_invalid_scope(desk,value):
    with pytest.raises(ValueError): scopes([value])

def test_expansion_conflict_rolls_back_original_claim(desk):
    a,b=register(desk),register(desk,'two')
    t=task(desk,a,['a']); task(desk,b,['b'])
    with pytest.raises(Conflict): desk.update(a['session_key'],t['id'],1,'RUNNING','Continue',resources=['b'])
    with pytest.raises(Conflict): task(desk,b,['a'])
    assert desk.snapshot('media-intelligence')['tasks'][-1]['version']==1

def test_owner_version_and_evidence(desk):
    a,b=register(desk),register(desk,'two')
    t=task(desk,a)
    with pytest.raises(PermissionError): desk.update(b['session_key'],t['id'],1,'RUNNING','Continue')
    with pytest.raises(ValueError): desk.update(a['session_key'],t['id'],1,'DONE','')
    t=desk.update(a['session_key'],t['id'],1,'BLOCKED','Waiting for review')
    with pytest.raises(Conflict): desk.update(a['session_key'],t['id'],1,'RUNNING','Continue')
    desk.update(a['session_key'],t['id'],t['version'],'DONE','',summary='Shipped UI',validation='Tests passed')
    assert task(desk,b)

def test_human_pause_blocks_agent_completion_and_handoff(desk):
    a,b=register(desk),register(desk,'two'); t=task(desk,a)
    t=desk.human('media-intelligence','pause',{'task_id':t['id'],'version':1})
    for state in ('RUNNING','DONE'):
        with pytest.raises(Conflict): desk.update(a['session_key'],t['id'],t['version'],state,'Continue',summary='Done',validation='tests')
    with pytest.raises(Conflict): desk.handoff(a['session_key'],t['id'],t['version'],b['session_id'])
    with pytest.raises(Conflict): task(desk,b)
    t=desk.human('media-intelligence','resume',{'task_id':t['id'],'version':t['version']})
    assert desk.update(a['session_key'],t['id'],t['version'],'RUNNING','Continue')

def test_stale_presence_and_restart_do_not_release_claims(desk):
    a=register(desk); task(desk,a)
    with desk.connection(True) as c:c.execute("UPDATE sessions SET last_seen='2000-01-01T00:00:00+00:00'")
    restored=Store(desk.path)
    assert restored.snapshot('media-intelligence')['sessions'][0]['stale']
    with pytest.raises(Conflict): task(restored,register(restored,'two'))

def test_broadcast_read_and_ack_are_separate_per_session(desk):
    a,b,c=register(desk),register(desk,'two','claude'),register(desk,'three','claude')
    mid=desk.message(a['session_key'],'claude','Please review')['message_id']
    assert desk.check_in(b['session_key'])['inbox']
    assert desk.check_in(b['session_key'])['inbox']
    with pytest.raises(PermissionError): desk.acknowledge(a['session_key'],mid)
    desk.acknowledge(b['session_key'],mid)
    assert not desk.check_in(b['session_key'])['inbox']
    assert desk.check_in(c['session_key'])['inbox']

def test_cursor_does_not_skip_large_history(desk):
    a=register(desk)
    for i in range(105):desk.note(a['session_key'],f'note {i}')
    first=desk.check_in(a['session_key'])
    assert len(first['events'])==100
    second=desk.check_in(a['session_key'],first['cursor'])
    assert len(second['events'])==6

def test_explicit_handoff_retains_claim_until_acceptance(desk):
    a,b,c=register(desk),register(desk,'two','claude'),register(desk,'three')
    t=task(desk,a); t=desk.handoff(a['session_key'],t['id'],1,b['session_id'])
    assert t['owner']==a['session_id']
    with pytest.raises(PermissionError): desk.handoff(c['session_key'],t['id'],t['version'],'',True)
    t=desk.handoff(b['session_key'],t['id'],t['version'],'',True)
    assert t['owner']==b['session_id']
    with pytest.raises(Conflict):task(desk,c)
    with pytest.raises(PermissionError):desk.update(a['session_key'],t['id'],t['version'],'RUNNING','Continue')

def test_queued_assignment_is_respected(desk):
    t=desk.human('media-intelligence','create',{'title':'Feature','resources':['src/a'],'next_step':'Build it','assigned_to':'claude'})
    with pytest.raises(Conflict):desk.claim(register(desk)['session_key'],'ignored',['src/a'],'Start',t['id'])
    t=desk.claim(register(desk,'two','claude')['session_key'],'ignored',['src/a'],'Start',t['id'])
    assert t['status']=='RUNNING'

def test_secrets_not_in_snapshot_and_notes_are_attributed(desk):
    a=register(desk)
    assert a['session_key'] not in str(desk.snapshot('media-intelligence'))
    assert 'secret_hash' not in str(desk.snapshot('media-intelligence'))
    with pytest.raises(ValueError):desk.note(a['session_key'],'I approve','decision')
    desk.note(a['session_key'],'Proposal','proposal')
    assert desk.snapshot('media-intelligence')['notes'][0]['author']==a['session_id']

def test_backup_restores_claims(desk,tmp_path):
    a=register(desk); t=task(desk,a); p=desk.backup(tmp_path/'backups')
    copy=Store(p)
    assert copy.snapshot('media-intelligence')['tasks'][0]['id']==t['id']
    with pytest.raises(Conflict):task(copy,register(copy,'other'))


def test_nextjs_brackets_are_literal_paths(desk):
    assert task(desk,register(desk),['src/app/[id]/page.tsx'])

def test_service_claim_crosses_project_boundary(desk):
    task(desk,register(desk),['service:mintel-app-deploy'])
    with pytest.raises(Conflict):
        task(desk,register(desk,'other',project='other-project'),['service:mintel-app-deploy'])

def test_dashboard_reassignment_activates_queued_work(desk):
    a=register(desk)
    t=desk.human('media-intelligence','create',{'title':'Queued work','resources':['src/a'],'next_step':'Build it'})
    t=desk.human('media-intelligence','reassign',{'task_id':t['id'],'version':1,'session_id':a['session_id'],'reason':'Explicit handoff'})
    assert t['status']=='RUNNING'
    assert t['owner']==a['session_id']

def test_legacy_import_preserves_active_and_completed_reports(desk,tmp_path):
    from import_legacy import import_roster
    source=tmp_path/'AGENTS.md';archive=tmp_path/'archive.md'
    original='''| Task / agent | Status | Worktree | Owned paths | Next action |
| feature / Claude | RUNNING / 2026-09-18 06:06 | `feat/example`; `/tmp/worktree` | `src/feature/` | Continue work |
| older / Codex | DONE / 2026-09-18 06:05 | `feat/old`; `/tmp/old` | `src/older` | Tests passed |
'''
    source.write_text(original)
    assert len(import_roster(desk,source,archive))==2
    assert source.read_text()==archive.read_text()==original
    board=desk.snapshot('media-intelligence')
    assert {t['status'] for t in board['tasks']}=={'RUNNING','DONE'}
    with pytest.raises(Conflict):task(desk,register(desk),['src/feature/view.ts'])
    assert task(desk,register(desk,'two'),['src/older'])
