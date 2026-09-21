"""Acknowledging a backlog in one call.

Registering into a busy project delivered 40 unread messages at once, and the
only way to clear them was 40 separate calls. These tests pin the batch form,
and — just as important — that the single-message form did not change, because
existing callers depend on both its return shape and its exception.
"""
import pytest
from store import Store

@pytest.fixture
def desk(tmp_path): return Store(tmp_path/'desk.sqlite3')

def register(desk,name='one',kind='codex',project='media-intelligence'):
    return desk.register(name,kind,project,'feat/test','/tmp/test-worktree')

def unread(desk,session):
    return [m['id'] for m in desk.check_in(session['session_key'],include=['inbox'])['inbox']]


def test_single_id_keeps_its_old_shape(desk):
    me,peer=register(desk),register(desk,'two','claude')
    m=desk.message(peer['session_key'],'all','hello')
    out=desk.acknowledge(me['session_key'],m['message_id'])
    assert out=={'message_id':m['message_id'],'acknowledged_by':me['session_id']}


def test_single_id_still_raises_for_someone_elses_message(desk):
    me,peer,third=register(desk),register(desk,'two','claude'),register(desk,'three','codex')
    m=desk.message(peer['session_key'],third['session_id'],'private')
    with pytest.raises(PermissionError):
        desk.acknowledge(me['session_key'],m['message_id'])


def test_a_backlog_clears_in_one_call(desk):
    me,peer=register(desk),register(desk,'two','claude')
    for i in range(40): desk.message(peer['session_key'],'all',f'message {i}')
    ids=unread(desk,me)
    assert len(ids)==40
    out=desk.acknowledge(me['session_key'],message_ids=ids)
    assert out['counts']=={'acknowledged':40,'failed':0}
    assert unread(desk,me)==[], 'the inbox should now be empty'


def test_one_bad_id_does_not_cost_the_rest(desk):
    """Aborting the batch is how a backlog never clears."""
    me,peer,third=register(desk),register(desk,'two','claude'),register(desk,'three','codex')
    good=[desk.message(peer['session_key'],'all',f'ok {i}')['message_id'] for i in range(3)]
    private=desk.message(peer['session_key'],third['session_id'],'not for me')['message_id']
    out=desk.acknowledge(me['session_key'],message_ids=good[:2]+[private,'m-does-not-exist']+good[2:])
    assert out['counts']=={'acknowledged':3,'failed':2}
    assert set(out['acknowledged'])==set(good)
    assert {f['message_id'] for f in out['failed']}=={private,'m-does-not-exist'}
    assert all(f['reason'] for f in out['failed']), 'a failure must say why'


def test_repeated_ids_are_acknowledged_once(desk):
    me,peer=register(desk),register(desk,'two','claude')
    m=desk.message(peer['session_key'],'all','hello')['message_id']
    out=desk.acknowledge(me['session_key'],message_ids=[m,m,m])
    assert out['acknowledged']==[m] and out['counts']['acknowledged']==1


def test_acknowledging_twice_is_harmless(desk):
    me,peer=register(desk),register(desk,'two','claude')
    m=desk.message(peer['session_key'],'all','hello')['message_id']
    desk.acknowledge(me['session_key'],m)
    out=desk.acknowledge(me['session_key'],message_ids=[m])
    assert out['counts']=={'acknowledged':1,'failed':0}


def test_a_single_string_is_accepted_as_a_batch(desk):
    me,peer=register(desk),register(desk,'two','claude')
    m=desk.message(peer['session_key'],'all','hello')['message_id']
    assert desk.acknowledge(me['session_key'],message_ids=m)['counts']['acknowledged']==1


def test_naming_nothing_is_refused(desk):
    me=register(desk)
    with pytest.raises(ValueError): desk.acknowledge(me['session_key'])
    with pytest.raises(ValueError): desk.acknowledge(me['session_key'],message_ids=[])
