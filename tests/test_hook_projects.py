import codex_hooks as hooks
from projects import Resolution

THREAD = '22222222-2222-2222-2222-222222222222'


def payload(cwd='/work/app', event='PreToolUse'):
    return {'session_id': THREAD, 'hook_event_name': event, 'cwd': cwd}


def test_declared_workspace_gets_the_onboarding_link(tmp_path):
    resolve = lambda cwd: Resolution('shop-app', 'file', '/work/app', 'Shop App', 'http://127.0.0.1:7399')
    out = hooks.enrollment_hint(payload(), 'claude', tmp_path, resolve=resolve,
                                fetch_state=lambda *a: (_ for _ in ()).throw(AssertionError('no fetch')))
    context = out['hookSpecificOutput']['additionalContext']
    assert 'http://127.0.0.1:7399/p/shop-app/onboard' in context
    assert THREAD in context and 'register_session without a project' in context


def test_unrelated_workspace_is_silent_and_cached(tmp_path):
    calls = {'resolve': 0, 'fetch': 0}

    def resolve(cwd):
        calls['resolve'] += 1
        return None

    def fetch_state(desk, project):
        calls['fetch'] += 1
        return {'sessions': []}
    assert hooks.enrollment_hint(payload(), 'claude', tmp_path, resolve, fetch_state) == {}
    assert hooks.enrollment_hint(payload(), 'claude', tmp_path, resolve, fetch_state) == {}
    assert calls == {'resolve': 1, 'fetch': 1}          # second call inside 5 minutes does nothing


def test_undeclared_workspace_with_legacy_sessions_still_gets_a_hint(tmp_path):
    fetch_state = lambda desk, project: {'sessions': [{'worktree': '/work/app'}]}
    out = hooks.enrollment_hint(payload('/work/app/src'), 'codex', tmp_path, lambda cwd: None, fetch_state)
    assert 'enable_notifications' in out['hookSpecificOutput']['additionalContext']


def test_resolver_errors_never_reach_the_agent(tmp_path):
    def broken(cwd):
        raise RuntimeError('boom')
    fetch_state = lambda desk, project: (_ for _ in ()).throw(OSError('down'))
    assert hooks.enrollment_hint(payload(), 'claude', tmp_path, broken, fetch_state) == {}


def test_desk_url_for():
    assert hooks.desk_url_for(None) == hooks.DEFAULT_DESK_URL
    assert hooks.desk_url_for(Resolution('x', 'file', '/r', '', 'http://h:1/')) == 'http://h:1'
