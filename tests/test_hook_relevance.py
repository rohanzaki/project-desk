"""What the lifecycle hook injects into an agent's context, every turn.

The hook used to re-inject every non-DONE task in the project on every user
prompt, because UserPromptSubmit counted as "initial". On a busy project that is
fifteen unrelated claims, truncated mid-sentence, on every single turn — costly,
and it buries the one line the agent actually had to act on.

A session start still gets the full board; that is the moment orientation is the
point. After that, another agent's task earns a line only when it is news.
"""
import pytest
from codex_hooks import collect
from store import Store

@pytest.fixture
def desk(tmp_path): return Store(tmp_path/'desk.sqlite3')

def register(desk,name,kind='claude',project='p'):
    return desk.register(name,kind,project,'b','/tmp/w')

def state_for(session,project='p'):
    return {'session_id':session['session_id'],'session_key':session['session_key'],
            'project':project,'agent':'claude','tasks':{},'messages':[]}

def response_for(desk,session):
    return desk.check_in(session['session_key'])

def settled(desk,session):
    """A session that has already seen the existing events.

    Reading from cursor 0 makes every prior event look like it just happened,
    so everything is legitimately 'news' once. The steady state a hook actually
    runs in is caught up — that is where re-injecting the board is the bug.
    """
    first=desk.check_in(session['session_key'])
    return desk.check_in(session['session_key'],since=first['cursor'])

def prefixes(lines):
    return [l.split(' ')[0]+(' '+l.split(' ')[1] if l.startswith('CONFLICTS') else '') for l in lines]


def test_session_start_shows_the_whole_active_board(desk):
    """Orientation is the point of a session start."""
    me,peer=register(desk,'me'),register(desk,'peer','codex')
    for i in range(6): desk.claim(peer['session_key'],f'Theirs {i}',[f'src/other{i}'],'n')
    lines,_=collect(state_for(me),response_for(desk,me),initial=True,full=True)
    assert sum(1 for l in lines if l.startswith('OTHER CLAIM'))==6


def test_an_ordinary_turn_does_not_redump_the_board(desk):
    """This is the regression that made every turn expensive."""
    me,peer=register(desk,'me'),register(desk,'peer','codex')
    for i in range(6): desk.claim(peer['session_key'],f'Theirs {i}',[f'src/other{i}'],'n')
    lines,_=collect(state_for(me),settled(desk,me),initial=True,full=False)
    assert [l for l in lines if l.startswith('OTHER CLAIM')]==[], \
        'unrelated claims must not be re-injected on every prompt'


def test_unrelated_claims_stay_out_once_seen(desk):
    """An agent holding its own work is not shown another agent's static claims.

    Deliberately NOT a "conflicts" test: claim_task refuses overlaps, so no
    collision can exist to be surfaced. would_conflict answers that question.
    """
    me,peer=register(desk,'me'),register(desk,'peer','codex')
    desk.claim(me['session_key'],'Mine',['src/app/cctv'],'n')
    desk.claim(peer['session_key'],'Unrelated',['src/lib/finance'],'n')
    lines,_=collect(state_for(me),settled(desk,me),initial=True,full=False)
    assert any(l.startswith('YOUR TASK') for l in lines)
    assert not any(l.startswith('OTHER CLAIM') for l in lines)


def test_my_own_tasks_survive_the_filter(desk):
    me=register(desk,'me')
    desk.claim(me['session_key'],'Mine',['src/mine'],'n')
    lines,_=collect(state_for(me),response_for(desk,me),initial=True,full=False)
    assert sum(1 for l in lines if l.startswith('YOUR TASK'))==1


def test_unread_messages_are_never_filtered_out(desk):
    """Task noise is optional; an unread message addressed to me is not."""
    me,peer=register(desk,'me'),register(desk,'peer','codex')
    for i in range(4): desk.claim(peer['session_key'],f'Theirs {i}',[f'src/other{i}'],'n')
    desk.message(peer['session_key'],'all','read me')
    lines,_=collect(state_for(me),response_for(desk,me),initial=True,full=False)
    assert sum(1 for l in lines if l.startswith('UNACKNOWLEDGED MESSAGE'))==1


def test_the_filter_measurably_shrinks_an_ordinary_turn(desk):
    """The reason this exists, asserted rather than assumed."""
    me,peer=register(desk,'me'),register(desk,'peer','codex')
    desk.claim(me['session_key'],'Mine',['src/mine'],'n')
    for i in range(15):
        t=desk.claim(peer['session_key'],f'Theirs {i}',[f'src/other{i}'],'n'*50)
        desk.update(peer['session_key'],t['id'],t['version'],'RUNNING','x'*200,summary='s'*300)
    full,_=collect(state_for(me),settled(desk,me),initial=True,full=True)
    lean,_=collect(state_for(me),settled(desk,me),initial=True,full=False)
    assert len('\n'.join(lean))*4 < len('\n'.join(full)), \
        f'expected a large cut, got {len(chr(10).join(full))} -> {len(chr(10).join(lean))}'
