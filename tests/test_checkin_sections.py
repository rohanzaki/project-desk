"""check_in's `include` parameter.

The default response carries the whole dashboard snapshot. That is correct for
the web UI and the lifecycle hooks, which read response['board'] directly, and
wrong for an agent that only wants to know what changed: on a busy project it
reached ~695KB and overran the caller's context twice in one night, so the one
unread message that mattered could not be read at all.

These tests pin both halves: the default shape must not move under the live
sessions that depend on it, and `include` must actually shrink the payload.
"""
import pytest
from store import Store

@pytest.fixture
def desk(tmp_path): return Store(tmp_path/'desk.sqlite3')

def register(desk,name='one',kind='codex',project='media-intelligence'):
    return desk.register(name,kind,project,'feat/test','/tmp/test-worktree')


def test_default_response_shape_is_unchanged(desk):
    """No include -> exactly the historic keys. Live peers read these."""
    a=register(desk)
    out=desk.check_in(a['session_key'])
    assert set(out)=={'session_id','cursor','events','inbox','board'}
    # enable_notifications reads this path directly; losing it breaks binding.
    assert 'sessions' in out['board']


def test_include_returns_only_what_was_asked_for(desk):
    a=register(desk)
    out=desk.check_in(a['session_key'],include=['inbox'])
    assert set(out)=={'session_id','cursor','inbox'}
    assert 'board' not in out, 'the snapshot is what makes the response enormous'


def test_a_single_section_may_be_passed_as_a_string(desk):
    a=register(desk)
    assert set(desk.check_in(a['session_key'],include='counts'))=={'session_id','cursor','counts'}


def test_unknown_section_is_refused_by_name(desk):
    """A typo must not silently return an empty response that reads as 'nothing new'."""
    a=register(desk)
    with pytest.raises(ValueError) as e:
        desk.check_in(a['session_key'],include=['inbox','bored'])
    assert 'bored' in str(e.value) and 'board' in str(e.value)


def test_inbox_bodies_are_never_shortened(desk):
    """Digesting the thing you are meant to read would recreate the original bug."""
    a,b=register(desk),register(desk,'two','claude')
    body='x'*5000
    desk.message(b['session_key'],'all',body)
    out=desk.check_in(a['session_key'],include=['inbox'])
    assert out['inbox'][0]['body']==body


def test_my_tasks_are_digested_but_report_what_was_cut(desk):
    a=register(desk)
    t=desk.claim(a['session_key'],'Long one',['src/thing'],'next')
    desk.update(a['session_key'],t['id'],t['version'],'RUNNING','next',summary='y'*4000)
    task=desk.check_in(a['session_key'],include=['my_tasks'])['my_tasks'][0]
    assert len(task['summary'])<=Store.DIGEST_CHARS+1
    assert task['summary_chars']==4000 and task['digested'] is True


def test_short_fields_are_left_alone(desk):
    a=register(desk)
    t=desk.claim(a['session_key'],'Short one',['src/short'],'do the thing')
    task=desk.check_in(a['session_key'],include=['my_tasks'])['my_tasks'][0]
    assert task['next_step']=='do the thing' and 'digested' not in task


def test_there_is_no_conflicts_section_because_it_could_only_ever_be_empty(desk):
    """claim() refuses overlaps, so no two tasks can hold overlapping resources.

    A 'conflicts' section would therefore always return [], and an agent reading
    that would believe it had checked something. The real question is answered by
    would_conflict() instead.
    """
    a=register(desk)
    assert 'conflicts' not in Store.SECTIONS
    with pytest.raises(ValueError):
        desk.check_in(a['session_key'],include=['conflicts'])


def test_would_conflict_names_the_holder_without_creating_anything(desk):
    mine,peer=register(desk),register(desk,'two','claude')
    desk.claim(peer['session_key'],'Theirs',['src/app/cctv'],'next')
    before=desk.check_in(mine['session_key'],include=['counts'])['counts']['project_tasks']
    # A directory claim covers its descendants.
    out=desk.would_conflict(mine['session_key'],['src/app/cctv/page.tsx'])
    assert out['clear'] is False
    assert out['blockers'][0]['owner']==peer['session_id']
    assert out['blockers'][0]['your_resources']==['src/app/cctv/page.tsx']
    assert desk.check_in(mine['session_key'],include=['counts'])['counts']['project_tasks']==before, \
        'the check must not create a task'


def test_would_conflict_is_clear_on_untouched_paths_and_on_my_own(desk):
    mine,peer=register(desk),register(desk,'two','claude')
    desk.claim(peer['session_key'],'Theirs',['src/lib/finance'],'next')
    desk.claim(mine['session_key'],'Mine',['src/app/cctv'],'next')
    assert desk.would_conflict(mine['session_key'],['src/app/newthing'])['clear'] is True
    # Re-checking something I already hold is not a conflict with myself.
    assert desk.would_conflict(mine['session_key'],['src/app/cctv'])['clear'] is True


def test_would_conflict_requires_a_resource(desk):
    a=register(desk)
    with pytest.raises(ValueError): desk.would_conflict(a['session_key'],[])


def test_counts_are_cheap_and_do_not_carry_prose(desk):
    a,b=register(desk),register(desk,'two','claude')
    desk.message(b['session_key'],'all','something to read')
    counts=desk.check_in(a['session_key'],include=['counts'])['counts']
    assert counts['unread_messages']==1
    assert counts['my_tasks']==0 and counts['sessions']==2


def test_cursor_holds_when_events_were_not_requested(desk):
    """Skipping events must not silently skip past them for good."""
    a,b=register(desk),register(desk,'two','claude')
    desk.claim(b['session_key'],'Noise',['src/noise'],'next')
    assert desk.check_in(a['session_key'],since=0,include=['inbox'])['cursor']==0
    assert desk.check_in(a['session_key'],since=0,include=['events'])['cursor']>0


def test_sections_measurably_shrink_the_response(desk):
    """The reason this parameter exists, asserted rather than assumed."""
    import json
    a,b=register(desk),register(desk,'two','claude')
    for i in range(25):
        t=desk.claim(b['session_key'],f'Task {i}',[f'src/mod{i}'],'next')
        desk.update(b['session_key'],t['id'],t['version'],'RUNNING','next',summary='z'*3000,validation='v'*3000)
    full=len(json.dumps(desk.check_in(a['session_key'])))
    lean=len(json.dumps(desk.check_in(a['session_key'],include=['inbox','counts'])))
    assert lean*10<full, f'expected a large reduction, got {full} -> {lean}'
