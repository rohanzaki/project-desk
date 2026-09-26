"""Desk feature requests: agents propose improvements to the desk itself.

Rohan, 2026-09-26 23:54: an agent that finds the desk slowing its work can ask for a
feature, any willing agent can build it, but only after the human approves. Requests are
global (the desk is shared by every project). Volunteering claims a task on
service:project-desk-dev, so only one desk change is in flight at a time.
"""
import re

from deskcore import Conflict, HUMAN, ident, now, text

REQUEST_SCHEMA = '''
    CREATE TABLE IF NOT EXISTS desk_requests (
        id TEXT PRIMARY KEY, origin_project TEXT NOT NULL, author TEXT NOT NULL,
        title TEXT NOT NULL, why TEXT NOT NULL, proposal TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'proposed', created TEXT NOT NULL, updated TEXT NOT NULL,
        decided TEXT, decided_by TEXT, decision_note TEXT NOT NULL DEFAULT '',
        volunteer TEXT, volunteer_project TEXT, task_id TEXT);
    CREATE INDEX IF NOT EXISTS desk_requests_status ON desk_requests(status, created);
    CREATE TABLE IF NOT EXISTS desk_request_support (
        request_id TEXT NOT NULL, session_id TEXT NOT NULL, project TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, PRIMARY KEY(request_id, session_id));
'''
REQUEST_STATUSES = ('proposed', 'approved', 'rejected', 'in_progress', 'done', 'withdrawn')
OPEN_STATUSES = ('proposed', 'approved', 'in_progress')
DEV_LANE = 'service:project-desk-dev'
_WORD = re.compile(r'[a-z0-9]{4,}')

CHECKLIST = (
    'You volunteered for an approved desk request. Build it in /home/rohanzakie/mi-coordination/project-desk '
    '(git repo, branch feat/project-desk): 1) read DESIGN.md, PROTOCOL.md and the tests near the code you touch; '
    '2) keep it additive: never break the MCP tools, the hooks or /api/state that live sessions use; '
    '3) run the whole suite in the foreground (.venv/bin/python -m pytest tests -q --ignore=tests/e2e.py) and add '
    'tests for the change; 4) dry-run the new code on a copy of data/desk.sqlite3 on another port; '
    '5) restart only by the procedure in /home/rohanzakie/mi-coordination/AGENTS.md "Restarting Project Desk": claim '
    'service:project-desk, announce_desk_restart, SendMessage every VS Code peer (Bidder included), back up the DB, '
    'restart, send a back note; 6) if agents\' routine changes, message bidder:all and pallet-level:all and update the '
    'rule MD files; 7) finish your task with evidence: the request shows as done.')


def _words(value):
    return set(_WORD.findall(str(value).lower()))


class RequestsMixin:
    REQUEST_TITLE_CHARS = 200
    REQUEST_BODY_CHARS = 4000

    def _migrate_requests(self, c):
        c.executescript(REQUEST_SCHEMA)

    # ---- reads -------------------------------------------------------------------
    def _request_rows(self, c, status=None, limit=100, ids=None):
        query, args = 'SELECT * FROM desk_requests', []
        if ids:
            query += f" WHERE id IN ({','.join('?' * len(ids))})"
            args += list(ids)
        query += ' ORDER BY created DESC LIMIT ?'
        rows = [dict(r) for r in c.execute(query, (*args, int(limit)))]
        if not rows:
            return []
        marks = ','.join('?' * len(rows))
        support = {}
        for r in c.execute(f'SELECT * FROM desk_request_support WHERE request_id IN ({marks}) ORDER BY created',
                           [r['id'] for r in rows]):
            support.setdefault(r['request_id'], []).append(dict(r))
        task_ids = [r['task_id'] for r in rows if r['task_id']]
        tasks = {}
        if task_ids:
            tasks = {r['id']: dict(r) for r in c.execute(
                f"SELECT id,status,title,summary,commit_ref FROM tasks WHERE id IN ({','.join('?' * len(task_ids))})", task_ids)}
        people = {r['author'] for r in rows} | {r['volunteer'] for r in rows if r['volunteer']} \
            | {s['session_id'] for v in support.values() for s in v} | {r['decided_by'] for r in rows if r['decided_by']}
        names = self._names(c, list(people))
        out = []
        for r in rows:
            task = tasks.get(r['task_id']) if r['task_id'] else None
            if task and task['status'] == 'DONE' and r['status'] == 'in_progress':
                r['status'] = 'done'   # the linked task finished: the request is done
                r['done_summary'] = task.get('summary') or ''
                r['done_commit'] = task.get('commit_ref') or ''
            r['task_status'] = task['status'] if task else ''
            r['author_name'] = names.get(r['author'], r['author'])
            r['volunteer_name'] = names.get(r['volunteer'], r['volunteer']) if r['volunteer'] else ''
            r['decided_by_name'] = names.get(r['decided_by'], r['decided_by']) if r['decided_by'] else ''
            r['support'] = [{**s, 'name': names.get(s['session_id'], s['session_id'])} for s in support.get(r['id'], [])]
            r['support_count'] = len(r['support'])
            out.append(r)
        if status:
            wanted = set(status if isinstance(status, (list, tuple)) else [status])
            out = [r for r in out if r['status'] in wanted]
        return out

    def _request(self, c, request_id):
        rows = self._request_rows(c, ids=[request_id], limit=1)
        if not rows:
            raise ValueError('Unknown desk request')
        return rows[0]

    def desk_requests(self, status=None, limit=100):
        """For the dashboard: every request, newest first (status filters after 'done' is derived)."""
        if status and status not in REQUEST_STATUSES:
            raise ValueError(f"status is one of {', '.join(REQUEST_STATUSES)}")
        with self.connection() as c:
            return self._request_rows(c, status=status, limit=max(1, min(int(limit), 500)))

    def list_desk_requests(self, key, status=None):
        with self.connection() as c:
            self.auth(c, key)
        return {'requests': self.desk_requests(status, 100),
                'note': 'Support an open request instead of filing a duplicate. Volunteer only for approved ones.'}

    def _similar(self, c, title, why, exclude=None):
        words = _words(title) | _words(why)
        if not words:
            return []
        found = []
        for r in c.execute(f"SELECT id,title,why,status FROM desk_requests WHERE status IN ({','.join('?' * len(OPEN_STATUSES))})",
                           OPEN_STATUSES):
            if r['id'] == exclude:
                continue
            shared = words & (_words(r['title']) | _words(r['why']))
            if len(shared) >= 2:
                found.append((len(shared), {'id': r['id'], 'title': r['title'], 'status': r['status']}))
        return [item for _, item in sorted(found, key=lambda x: -x[0])[:5]]

    # ---- agent writes -------------------------------------------------------------
    def request_desk_feature(self, key, title, why, proposal=''):
        title = text(title, 'title', self.REQUEST_TITLE_CHARS)
        why = text(why, 'why', self.REQUEST_BODY_CHARS)
        proposal = (proposal or '').strip()[:self.REQUEST_BODY_CHARS]
        with self.connection(True) as c:
            s = self.auth(c, key)
            similar = self._similar(c, title, why)
            rid, stamp = ident('dr-'), now()
            c.execute('''INSERT INTO desk_requests(id,origin_project,author,title,why,proposal,created,updated)
                         VALUES(?,?,?,?,?,?,?,?)''', (rid, s['project'], s['id'], title, why, proposal, stamp, stamp))
            self.event(c, s['project'], s['id'], 'desk_request.created', {'request_id': rid, 'title': title})
            return {'request': self._request(c, rid), 'similar': similar,
                    'note': ('It waits for the human owner on the dashboard. Nobody builds it before approval.'
                             + (' Similar open requests exist: if one is the same, withdraw_desk_request this one and '
                                'support_desk_request that one.' if similar else ''))}

    def support_desk_request(self, key, request_id, note=''):
        with self.connection(True) as c:
            s = self.auth(c, key)
            r = self._request(c, request_id)
            if r['status'] not in OPEN_STATUSES:
                raise Conflict(f"Request {request_id} is {r['status']}; support only open requests")
            c.execute('''INSERT INTO desk_request_support(request_id,session_id,project,note,created) VALUES(?,?,?,?,?)
                         ON CONFLICT(request_id,session_id) DO UPDATE SET note=excluded.note''',
                      (request_id, s['id'], s['project'], (note or '').strip()[:1000], now()))
            c.execute('UPDATE desk_requests SET updated=? WHERE id=?', (now(), request_id))
            self.event(c, s['project'], s['id'], 'desk_request.supported', {'request_id': request_id})
            return self._request(c, request_id)

    def withdraw_desk_request(self, key, request_id, reason=''):
        with self.connection(True) as c:
            s = self.auth(c, key)
            r = self._request(c, request_id)
            if r['author'] not in self._aliases(c, s['id']):
                raise PermissionError('Only the author can withdraw a request')
            if r['status'] != 'proposed':
                raise Conflict(f"Request {request_id} is {r['status']}; only a proposed request can be withdrawn")
            c.execute("UPDATE desk_requests SET status='withdrawn',updated=?,decision_note=? WHERE id=?",
                      (now(), (reason or '').strip()[:1000], request_id))
            self.event(c, s['project'], s['id'], 'desk_request.withdrawn', {'request_id': request_id})
            return self._request(c, request_id)

    def volunteer_desk_request(self, key, request_id):
        # Reserve first, in its own transaction: two volunteers cannot both win.
        with self.connection(True) as c:
            s = self.auth(c, key)
            r = self._request(c, request_id)
            if r['status'] == 'proposed':
                raise PermissionError('The human owner has not approved this request yet. Support it instead; '
                                      'volunteer after it is approved.')
            reserved = c.execute("UPDATE desk_requests SET status='in_progress',volunteer=?,volunteer_project=?,updated=? "
                                 "WHERE id=? AND status='approved' AND volunteer IS NULL",
                                 (s['id'], s['project'], now(), request_id)).rowcount
            if not reserved:
                who = f" by {r['volunteer_name']}" if r['volunteer'] else ''
                raise Conflict(f"Request {request_id} is {r['status']}{who}; it cannot be volunteered for")
        try:
            task = self.claim(key, f"Desk request {request_id}: {r['title']}"[:300], [DEV_LANE],
                              'Build the approved desk request; follow the checklist in its volunteer reply')
        except Exception:
            with self.connection(True) as c:   # give the request back
                c.execute("UPDATE desk_requests SET status='approved',volunteer=NULL,volunteer_project=NULL,updated=? "
                          "WHERE id=? AND volunteer=?", (now(), request_id, s['id']))
            raise
        with self.connection(True) as c:
            c.execute('UPDATE desk_requests SET task_id=?,updated=? WHERE id=?', (task['id'], now(), request_id))
            self.event(c, s['project'], s['id'], 'desk_request.volunteered', {'request_id': request_id, 'task_id': task['id']})
            return {'request': self._request(c, request_id), 'task': task, 'checklist': CHECKLIST}

    # ---- the human -----------------------------------------------------------------
    def _decide_desk_request(self, c, project, data, approve):
        request_id = data.get('request_id', '')
        r = self._request(c, request_id)
        if r['status'] != 'proposed':
            raise Conflict(f"Request {request_id} is already {r['status']}")
        note = (data.get('note') or '').strip()[:1000]
        status = 'approved' if approve else 'rejected'
        c.execute('UPDATE desk_requests SET status=?,decided=?,decided_by=?,decision_note=?,updated=? WHERE id=?',
                  (status, now(), HUMAN, note, now(), request_id))
        self.event(c, r['origin_project'], HUMAN, f'desk_request.{status}', {'request_id': request_id})
        if approve:
            body = (f"DESK REQUEST APPROVED {request_id}: {r['title']}. Asked by {r['author_name']} "
                    f"({r['origin_project']}).{' Note: ' + note if note else ''} Any willing agent can build it: "
                    f"volunteer_desk_request('{request_id}') (first come; it claims {DEV_LANE}).")
            self._broadcast_all(c, r['origin_project'], HUMAN, body)
        else:
            self._message(c, r['origin_project'], HUMAN, r['author'],
                          f"Your desk request {request_id} ({r['title']}) was not approved." + (f' Reason: {note}' if note else ''))
        return self._request(c, request_id)

    def _request_attention(self, c, include_ids=None):
        """Proposed requests as 'Needs you' items (global: shown once, tagged with the asking project)."""
        items = []
        for r in self._request_rows(c, status='proposed', limit=50):
            items.append({'key': f"desk_request:{r['id']}", 'project': r['origin_project'], 'kind': 'desk_request',
                          'request_id': r['id'], 'title': r['title'], 'body': r['why'], 'proposal': r['proposal'],
                          'meta': f"Desk request · {r['support_count']} backing" + (f" · {r['why'][:120]}" if r['why'] else ''),
                          'task_id': None, 'task_title': '', 'who': {'id': r['author'], 'name': r['author_name']},
                          'created': r['created'], 'support_count': r['support_count']})
        return items
