"""Dashboard v2 server surface: BoardMixin.

Read-only, bounded aggregate endpoints (board, task, attention, digest,
search, events, lanes) for the human dashboard, plus refused-claim recording
and the human actions the v2 page needs (ack.inbox, answer, refusal.*,
snooze.*). See design/redesign-2026-09-26/SPEC.md, "Server contracts".

Every table here is new (refusals, snoozes), created with CREATE TABLE IF NOT
EXISTS so an earlier release still opens this database. Nothing here changes
an existing table's columns, the MCP tools, codex_hooks.py or /api/state.
"""
import json
import re
import time
from datetime import datetime, timedelta, timezone

from deskcore import Conflict, HUMAN, ident, now, text
from desk_features import GLOBAL, _ago, _first_line

BOARD_SCHEMA = '''
    CREATE TABLE IF NOT EXISTS refusals (
        id TEXT PRIMARY KEY, project TEXT NOT NULL, session_id TEXT NOT NULL, title TEXT NOT NULL,
        resources TEXT NOT NULL, holder_task_id TEXT NOT NULL, holder_session TEXT NOT NULL DEFAULT '',
        holder_project TEXT NOT NULL DEFAULT '', created TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open', resolved TEXT, resolved_by TEXT);
    CREATE INDEX IF NOT EXISTS refusals_project ON refusals(project, status);
    CREATE INDEX IF NOT EXISTS refusals_session ON refusals(session_id);
    CREATE INDEX IF NOT EXISTS refusals_holder ON refusals(holder_task_id);
    CREATE TABLE IF NOT EXISTS snoozes (
        project TEXT NOT NULL, key TEXT NOT NULL, until TEXT NOT NULL, created TEXT NOT NULL,
        PRIMARY KEY(project, key));
'''

# claim_resources' exact message (deskcore.overlaps via store.claim_resources):
#   f"Resource overlaps {existing['resource']} held by {existing['task_id']}. Request a handoff; do not edit."
_OVERLAP_RE = re.compile(r'^Resource overlaps (.+) held by (t-[0-9a-f]{12})\.')

_ATTENTION_ORDER = {'approval': 0, 'question': 1, 'blocked': 2, 'stale': 3, 'refused': 4, 'action': 5}
_BLOCKED_RE = re.compile(
    r'\b(rohan|you|your|human|owner|decision|decide|approve|approval|go.?ahead|ok to|confirm)\b', re.I)


def _snippet(value, q, width=160):
    """160-char excerpt around the first case-insensitive match of q in value."""
    value = value or ''
    if not value:
        return ''
    low, qlow = value.lower(), q.lower()
    idx = low.find(qlow)
    if idx == -1:
        return value[:width] + ('…' if len(value) > width else '')
    start = max(0, idx - width // 2)
    end = min(len(value), start + width)
    start = max(0, end - width)
    return ('…' if start > 0 else '') + value[start:end] + ('…' if end < len(value) else '')


class BoardMixin:
    """New tables, refusal recording, board/task/attention/digest/search/events/lanes reads,
    and the extra human actions the v2 dashboard needs. Combined into Store alongside
    FeaturesMixin; every method here may call FeaturesMixin/Store helpers via self."""

    # ---- migration ----------------------------------------------------------

    def _migrate_board(self, c):
        c.executescript(BOARD_SCHEMA)

    # ---- refused claims -------------------------------------------------------
    #
    # store.claim() wraps claim_resources() in a try/except Conflict. When the
    # message is an overlap refusal, this records it in a SEPARATE write
    # transaction (the failing one has already rolled back) and the original
    # Conflict is re-raised unchanged — the agent's error text never changes.

    def _record_refusal(self, s, title, resources, message):
        match = _OVERLAP_RE.match(message)
        if not match:
            return
        resource, holder_task_id = match.group(1), match.group(2)
        try:
            with self.connection(True) as c:
                holder = c.execute('SELECT project, owner FROM tasks WHERE id=?', (holder_task_id,)).fetchone()
                holder_project = holder['project'] if holder else ''
                holder_session = holder['owner'] if holder else ''
                rid = ident('r-')
                c.execute('''INSERT INTO refusals(id,project,session_id,title,resources,holder_task_id,
                             holder_session,holder_project,created,status)
                             VALUES(?,?,?,?,?,?,?,?,?,'open')''',
                          (rid, s['project'], s['id'], (title or '')[:300], json.dumps(resources),
                           holder_task_id, holder_session or '', holder_project or '', now()))
                self.event(c, s['project'], s['id'], 'claim.refused',
                          {'refusal_id': rid, 'resource': resource, 'holder_task_id': holder_task_id,
                           'holder_session': holder_session, 'holder_project': holder_project})
        except Exception:
            pass   # the caller's original Conflict must reach the agent unchanged either way

    def _who(self, c, session_id):
        if not session_id:
            return None
        if session_id == HUMAN:
            return {'id': HUMAN, 'name': 'Human', 'kind': 'human'}
        row = c.execute('SELECT id,name,kind FROM sessions WHERE id=?', (session_id,)).fetchone()
        return dict(row) if row else {'id': session_id, 'name': self._display_name(c, session_id), 'kind': ''}

    def _with_acknowledgments(self, c, messages):
        ids = [m['id'] for m in messages]
        if not ids:
            return messages
        acks = {}
        for chunk in range(0, len(ids), 500):
            part = ids[chunk:chunk + 500]
            marks = ','.join('?' * len(part))
            for r in c.execute(f'SELECT message_id,session_id,acknowledged FROM receipts '
                               f'WHERE message_id IN ({marks})', part):
                acks.setdefault(r['message_id'], []).append(
                    {'session_id': r['session_id'], 'acknowledged': r['acknowledged']})
        for m in messages:
            m['acknowledgments'] = acks.get(m['id'], [])
        return messages

    # ---- GET /api/board -------------------------------------------------------

    def _current_etag(self, c):
        max_seq = c.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        return f'"{max_seq}-{int(time.time() // 60)}"'

    def board_etag(self):
        with self.connection() as c:
            return self._current_etag(c)

    def board(self, project, done_days=7):
        with self.connection() as c:
            etag = self._current_etag(c)
            task_owners = {r[0] for r in c.execute(
                "SELECT DISTINCT owner FROM tasks WHERE project=? AND status!='DONE' AND owner IS NOT NULL",
                (project,))}
            cutoff7 = _ago(hours=24 * 7)
            sessions = []
            for r in c.execute('SELECT id,name,kind,branch,worktree,last_seen FROM sessions '
                               'WHERE project=? AND imported=0', (project,)):
                if r['last_seen'] > cutoff7 or r['id'] in task_owners:
                    item = dict(r)
                    item['stale'] = (datetime.now(timezone.utc) -
                                     datetime.fromisoformat(r['last_seen'])).total_seconds() > 900
                    sessions.append(item)
            done_cutoff = _ago(hours=24 * done_days)
            task_rows = c.execute("SELECT id FROM tasks WHERE project=? AND (status!='DONE' OR updated>?) "
                                  'ORDER BY updated DESC', (project, done_cutoff)).fetchall()
            tasks = [self.task(c, r['id']) for r in task_rows]
            tasks_done_total = c.execute("SELECT COUNT(*) FROM tasks WHERE project=? AND status='DONE'",
                                         (project,)).fetchone()[0]
            tasks_done_shown = sum(1 for t in tasks if t['status'] == 'DONE')
            messages = self._with_acknowledgments(c, self._decorate(c, [dict(r) for r in c.execute(
                'SELECT * FROM messages WHERE project=? ORDER BY created DESC LIMIT 100', (project,))]))
            outgoing = self._decorate(c, [dict(r) for r in c.execute(
                '''SELECT m.* FROM messages m JOIN message_links l ON l.message_id=m.id
                   WHERE l.origin_project=? AND m.project!=? ORDER BY m.created DESC LIMIT 30''',
                (project, project))])
            for m in outgoing:
                m['to_project'] = m['project']
            outgoing = self._with_acknowledgments(c, outgoing)
            handoffs = [h for h in self._handoffs(c, project, limit=100) if h['status'] == 'offered']
            refusals = [dict(r) for r in c.execute(
                "SELECT * FROM refusals WHERE project=? AND status='open' ORDER BY created DESC LIMIT 50",
                (project,))]
            for r in refusals:
                r['resources'] = json.loads(r['resources'])
            snoozes = {r['key']: r['until'] for r in c.execute(
                'SELECT key, until FROM snoozes WHERE project=? AND until>?', (project, now()))}
            return {
                'project': project, 'generated_at': now(), 'version': etag,
                'sessions': sessions, 'tasks': tasks,
                'tasks_done_total': tasks_done_total, 'tasks_done_shown': tasks_done_shown,
                'evidence': self._evidence_summary(c, [t['id'] for t in tasks]),
                'messages': messages, 'outgoing_messages': outgoing,
                'notes': [dict(r) for r in c.execute(
                    'SELECT * FROM notes WHERE project=? ORDER BY created DESC LIMIT 60', (project,))],
                'lessons': self._board_lessons(c, project),
                'approvals': self._board_approvals(c, project),
                'open_questions': [{'message_id': r['message_id'], 'asker': r['asker'],
                                    'recipient': r['recipient'], 'created': r['created']}
                                   for r in c.execute(
                                       "SELECT * FROM questions WHERE project=? AND status='open' "
                                       'ORDER BY created DESC LIMIT 50', (project,))],
                'queues': [{'resource': r['resource'], 'queue': self._queue_view(c, r['resource'], project)}
                          for r in c.execute("SELECT DISTINCT resource FROM resource_queue WHERE project=? "
                                            "OR resource LIKE 'service:%'", (project,))],
                'prod': self._prod(c)['services'],
                'shared_locks': [{'resource': r['resource'], 'task_id': r['task_id'], 'project': r['project']}
                                 for r in c.execute(
                                     "SELECT * FROM claims WHERE resource LIKE 'service:%' AND project!=?",
                                     (project,))],
                'crossovers': self._crossovers_for(c, project, include_closed=True),
                'stale_claims': self._stale(c, project, limit=50),
                'action_items': self._board_actions(c, project),
                'handoff_briefs': handoffs,
                'refusals': refusals,
                'snoozes': snoozes,
            }

    # ---- GET /api/task ---------------------------------------------------------

    def task_detail(self, project, task_id):
        with self.connection() as c:
            t = self.task(c, task_id)
            if t['project'] != project:
                raise ValueError('Task belongs to another project')
            journal = self._journal(c, task_id, 80)
            messages = self._with_acknowledgments(c, self._decorate(c, [dict(r) for r in c.execute(
                'SELECT * FROM messages WHERE task_id=? ORDER BY created', (task_id,))]))
            action_items = [self._action_dict(c, r) for r in c.execute(
                'SELECT * FROM action_items WHERE task_id=? ORDER BY created', (task_id,))]
            evidence = self._evidence_list(c, task_id)
            lessons = self._lessons_for_paths(c, project, t['resources'])
            handoff_briefs = self._handoffs(c, project, task_id, self.CONTEXT_HANDOFF_LIMIT)
            crossover_id = self._crossover_of_task(c, task_id)
            crossover = self._crossover_view(c, crossover_id) if crossover_id else None
            return {'task': t, 'journal': journal, 'messages': messages, 'action_items': action_items,
                    'evidence': evidence, 'lessons': lessons, 'handoff_briefs': handoff_briefs,
                    'crossover': crossover}

    # ---- GET /api/attention -----------------------------------------------------

    def _attention_for_project(self, c, project):
        items = []
        for r in c.execute("SELECT * FROM approvals WHERE project=? AND status='pending'", (project,)):
            task_title = ''
            if r['task_id']:
                trow = c.execute('SELECT title FROM tasks WHERE id=?', (r['task_id'],)).fetchone()
                task_title = trow['title'] if trow else ''
            items.append({'key': f"approval:{r['id']}", 'project': project, 'kind': 'approval',
                          'title': r['title'], 'meta': r['context'][:200], 'task_id': r['task_id'],
                          'task_title': task_title, 'who': self._who(c, r['requester']), 'created': r['created'],
                          'approval_id': r['id'], 'options': json.loads(r['options'])})
        for r in c.execute("SELECT q.*, m.body FROM questions q JOIN messages m ON m.id=q.message_id "
                           "WHERE q.project=? AND q.status='open' AND q.recipient IN (?,'human')",
                           (project, HUMAN)):
            task_title = ''
            if r['task_id']:
                trow = c.execute('SELECT title FROM tasks WHERE id=?', (r['task_id'],)).fetchone()
                task_title = trow['title'] if trow else ''
            items.append({'key': f"question:{r['message_id']}", 'project': project, 'kind': 'question',
                          'title': _first_line(r['body'], 120), 'meta': '', 'task_id': r['task_id'],
                          'task_title': task_title, 'who': self._who(c, r['asker']), 'created': r['created'],
                          'message_id': r['message_id'], 'body': r['body']})
        stale_rows = self._stale(c, project)
        owner_kinds = {}
        owners = {r['owner'] for r in stale_rows}
        if owners:
            marks = ','.join('?' * len(owners))
            owner_kinds = {row['id']: row['kind'] for row in c.execute(
                f'SELECT id,kind FROM sessions WHERE id IN ({marks})', list(owners))}
        for r in stale_rows:
            items.append({'key': f"stale:{r['task_id']}", 'project': project, 'kind': 'stale',
                          'title': r['title'], 'meta': '', 'task_id': r['task_id'], 'task_title': r['title'],
                          'who': {'id': r['owner'], 'name': r['owner_name'], 'kind': owner_kinds.get(r['owner'], '')},
                          'created': r['owner_last_seen'], 'resources': r['resources'],
                          'owner_last_seen': r['owner_last_seen']})
        for r in c.execute("SELECT * FROM refusals WHERE project=? AND status='open' ORDER BY created DESC",
                           (project,)):
            items.append({'key': f"refused:{r['id']}", 'project': project, 'kind': 'refused',
                          'title': r['title'], 'meta': '', 'task_id': None, 'task_title': '',
                          'who': self._who(c, r['session_id']), 'created': r['created'],
                          'refusal_id': r['id'], 'resources': json.loads(r['resources']),
                          'holder_task_id': r['holder_task_id'], 'holder_session': r['holder_session'],
                          'holder_name': self._display_name(c, r['holder_session']) if r['holder_session'] else ''})
        action_rows = c.execute("SELECT * FROM action_items WHERE project=? AND status='open' AND assignee=?",
                                (project, HUMAN)).fetchall()
        groups = {}
        for r in action_rows:
            groups.setdefault(r['task_id'], []).append(r)
        for task_id, rows in groups.items():
            rows = sorted(rows, key=lambda r: r['created'], reverse=True)
            task_title = ''
            if task_id:
                trow = c.execute('SELECT title FROM tasks WHERE id=?', (task_id,)).fetchone()
                task_title = trow['title'] if trow else ''
            items.append({'key': f"action:{project}:{task_id or 'none'}", 'project': project, 'kind': 'action',
                          'title': f'{len(rows)} action item(s)', 'meta': rows[0]['body'][:200],
                          'task_id': task_id, 'task_title': task_title, 'who': self._who(c, rows[0]['author']),
                          'created': rows[0]['created'], 'item_ids': [r['id'] for r in rows],
                          'bodies': [r['body'] for r in rows]})
        covered = {r[0] for r in c.execute(
            "SELECT task_id FROM approvals WHERE project=? AND status='pending' AND task_id IS NOT NULL",
            (project,))}
        covered |= {r[0] for r in c.execute(
            "SELECT task_id FROM questions WHERE project=? AND status='open' AND task_id IS NOT NULL",
            (project,))}
        for r in c.execute("SELECT * FROM tasks WHERE project=? AND status='BLOCKED'", (project,)):
            if r['id'] in covered or not _BLOCKED_RE.search(r['next_step'] or ''):
                continue
            items.append({'key': f"blocked:{r['id']}", 'project': project, 'kind': 'blocked',
                          'title': r['title'], 'meta': '', 'task_id': r['id'], 'task_title': r['title'],
                          'who': self._who(c, r['owner']) if r['owner'] else None, 'created': r['updated'],
                          'next_step': r['next_step']})
        return items

    def attention(self, projects='all', include_snoozed=False):
        with self.connection() as c:
            if not projects or projects == 'all':
                plist = [r[0] for r in c.execute('SELECT slug FROM projects WHERE archived=0 ORDER BY slug')]
            else:
                plist = [p.strip() for p in projects.split(',') if p.strip()]
                if not plist:
                    raise ValueError('projects must name at least one project, or "all"')
                for p in plist:
                    if not c.execute('SELECT 1 FROM projects WHERE slug=?', (p,)).fetchone():
                        raise ValueError(f'Unknown project {p}')
            items = []
            for project in plist:
                items.extend(self._attention_for_project(c, project))
            active = {(r['project'], r['key']): r['until']
                     for r in c.execute('SELECT project,key,until FROM snoozes WHERE until>?', (now(),))}
            if include_snoozed:
                for item in items:
                    until = active.get((item['project'], item['key']))
                    if until:
                        item['snoozed_until'] = until
            else:
                items = [i for i in items if (i['project'], i['key']) not in active]
            items.sort(key=lambda i: i['created'], reverse=True)
            items.sort(key=lambda i: _ATTENTION_ORDER[i['kind']])
            counts = {}
            for i in items:
                counts[i['project']] = counts.get(i['project'], 0) + 1
            return {'items': items, 'counts': counts, 'total': len(items)}

    # ---- GET /api/digest --------------------------------------------------------

    def digest(self, project, since):
        until = now()
        with self.connection() as c:
            done_rows = c.execute(
                "SELECT id,title,summary,owner,updated FROM tasks WHERE project=? AND status='DONE' AND updated>? "
                'ORDER BY updated DESC LIMIT 20', (project, since)).fetchall()
            dn = self._names(c, [r['owner'] for r in done_rows])
            done = [{'task_id': r['id'], 'title': r['title'], 'owner_name': dn.get(r['owner'], r['owner']),
                    'summary': r['summary'], 'updated': r['updated']} for r in done_rows]
            deploy_rows = c.execute(
                'SELECT service,commit_ref,summary,session_id,created FROM deploys WHERE project=? AND created>? '
                'ORDER BY created DESC LIMIT 20', (project, since)).fetchall()
            depn = self._names(c, [r['session_id'] for r in deploy_rows])
            deploys = [{'service': r['service'], 'commit_ref': r['commit_ref'], 'summary': r['summary'],
                       'session_name': depn.get(r['session_id'], r['session_id']), 'created': r['created']}
                      for r in deploy_rows]
            claimed_rows = c.execute(
                "SELECT tl.task_id,tl.created,t.title,t.owner FROM task_log tl JOIN tasks t ON t.id=tl.task_id "
                "WHERE tl.project=? AND tl.kind='claimed' AND tl.created>? ORDER BY tl.created DESC LIMIT 20",
                (project, since)).fetchall()
            cn = self._names(c, [r['owner'] for r in claimed_rows])
            claimed = [{'task_id': r['task_id'], 'title': r['title'], 'owner_name': cn.get(r['owner'], r['owner']),
                       'created': r['created']} for r in claimed_rows]
            question_rows = c.execute(
                'SELECT message_id,asker,recipient,task_id,created FROM questions WHERE project=? AND created>? '
                'ORDER BY created DESC LIMIT 20', (project, since)).fetchall()
            qn = self._names(c, [r['asker'] for r in question_rows])
            questions = [{'message_id': r['message_id'], 'asker_name': qn.get(r['asker'], r['asker']),
                         'recipient': r['recipient'], 'task_id': r['task_id'], 'created': r['created']}
                        for r in question_rows]
            approval_rows = c.execute(
                'SELECT * FROM approvals WHERE project=? AND created>? ORDER BY created DESC LIMIT 20',
                (project, since)).fetchall()
            an = self._names(c, [r['requester'] for r in approval_rows])
            approvals = [{'approval_id': r['id'], 'title': r['title'],
                         'requester_name': an.get(r['requester'], r['requester']),
                         'task_id': r['task_id'], 'created': r['created']} for r in approval_rows]
            decision_rows = c.execute(
                "SELECT * FROM notes WHERE project=? AND kind IN ('decision','changelog') AND created>? "
                'ORDER BY created DESC LIMIT 20', (project, since)).fetchall()
            decisions = [{'note_id': r['id'], 'kind': r['kind'], 'body': r['body'][:300], 'created': r['created']}
                        for r in decision_rows]
            messages_to_you = c.execute(
                "SELECT COUNT(*) FROM messages WHERE project=? AND recipient IN (?,'all') AND created>?",
                (project, HUMAN, since)).fetchone()[0]
            return {'since': since, 'until': until, 'done': done, 'deploys': deploys, 'claimed': claimed,
                    'questions': questions, 'approvals': approvals, 'decisions': decisions,
                    'messages_to_you': messages_to_you}

    # ---- GET /api/search ---------------------------------------------------------

    def search(self, project, q):
        q = (q or '').strip()
        if len(q) < 2:
            raise ValueError('q must be at least 2 characters')
        like = f'%{q}%'
        with self.connection() as c:
            tasks = []
            for r in c.execute(
                    "SELECT * FROM tasks WHERE project=? AND (title LIKE ? OR next_step LIKE ? OR summary LIKE ? "
                    'OR resources LIKE ? OR id LIKE ?) ORDER BY updated DESC LIMIT 10',
                    (project, like, like, like, like, like)):
                item = dict(r)
                item['resources'] = json.loads(item['resources'])
                joined_resources = ', '.join(item['resources'])
                hay = next((item[f] for f in ('title', 'next_step', 'summary')
                           if item.get(f) and q.lower() in item[f].lower()), None)
                if hay is None:
                    hay = joined_resources if q.lower() in joined_resources.lower() else item['id']
                item['snippet'] = _snippet(hay, q)
                tasks.append(item)
            messages = []
            for r in c.execute('SELECT * FROM messages WHERE project=? AND body LIKE ? '
                               'ORDER BY created DESC LIMIT 10', (project, like)):
                item = dict(r)
                item['snippet'] = _snippet(item['body'], q)
                messages.append(item)
            messages = self._decorate(c, messages)
            notes = []
            for r in c.execute('SELECT * FROM notes WHERE project=? AND body LIKE ? '
                               'ORDER BY created DESC LIMIT 10', (project, like)):
                item = dict(r)
                item['snippet'] = _snippet(item['body'], q)
                notes.append(item)
            lessons = []
            for r in c.execute(
                    'SELECT * FROM lessons WHERE archived=0 AND project IN (?,?) AND (body LIKE ? OR paths LIKE ?) '
                    'ORDER BY updated DESC LIMIT 10', (project, GLOBAL, like, like)):
                item = self._lesson_row(c, r)
                hay = item['body'] if q.lower() in item['body'].lower() else ', '.join(item['paths'])
                item['snippet'] = _snippet(hay, q)
                lessons.append(item)
            return {'tasks': tasks, 'messages': messages, 'notes': notes, 'lessons': lessons}

    # ---- GET /api/events ---------------------------------------------------------

    def _event_text(self, kind, data, titles):
        tid = data.get('task_id')
        title = (titles.get(tid, '') if tid else '') or ''
        if kind == 'task.claimed':
            return f"claimed {tid} {title} ({', '.join(data.get('resources', []))})".strip()
        if kind == 'task.updated':
            detail = data.get('summary') or data.get('next_step') or ''
            return f"{tid} {title} → {data.get('status', '')}: {detail}"[:220].strip()
        if kind == 'task.reopened':
            return f"reopened {tid} {title}".strip()
        if kind.startswith('task.'):
            return f"{kind[len('task.'):]} {tid} {title}".strip()
        if kind == 'message.sent':
            return f"sent {data.get('message_id', '')} to {data.get('recipient', '')}"
        if kind == 'message.sent_cross':
            return f"sent {data.get('message_id', '')} to {data.get('to_project', '')}:{data.get('recipient', '')}"
        if kind == 'message.acknowledged':
            return f"read {data.get('message_id', '')}"
        if kind == 'deploy.recorded':
            return f"deployed {data.get('service', '')} at {data.get('commit_ref', '')}"
        if kind == 'lesson.created':
            paths = data.get('paths') or []
            return 'remembered a lesson' + (f" ({', '.join(paths)})" if paths else '')
        if kind == 'lesson.archived':
            return f"archived a lesson: {data.get('reason', '')}"[:160]
        if kind == 'note.created':
            return f"noted ({data.get('kind', '')})"
        if kind == 'approval.requested':
            return f"asked for approval: {data.get('title', '')}"
        if kind == 'approval.decided':
            return f"decided {data.get('approval_id', '')}: {data.get('decision', '')}"
        if kind == 'crossover.started':
            return f"started crossover {data.get('crossover_id', '')} on {tid}"
        if kind == 'crossover.invited':
            return f"invited into crossover {data.get('crossover_id', '')} from {data.get('from_project', '')}"
        if kind == 'crossover.joined':
            return f"joined crossover {data.get('crossover_id', '')} with {tid}"
        if kind == 'crossover.signed_off':
            return f"signed off crossover {data.get('crossover_id', '')}"
        if kind == 'crossover.contract':
            return f"published contract v{data.get('version', '')} for {data.get('crossover_id', '')}"
        if kind in ('handoff.offered', 'handoff.accepted', 'handoff.prepared'):
            return f"{kind.split('.')[1]} handoff for {tid}"
        if kind == 'action.added':
            return f"added {len(data.get('items', []))} action item(s) for {data.get('assignee', '')}"
        if kind == 'action.resolved':
            return f"resolved action item {data.get('item_id', '')}: {data.get('status', '')}"
        if kind == 'action.superseded':
            return f"superseded {len(data.get('items', []))} action item(s)"
        if kind == 'queue.joined':
            return f"queued for {data.get('resource', '')}"
        if kind == 'session.registered':
            return f"registered ({data.get('kind', '')})"
        if kind == 'session.resumed':
            return f"resumed {data.get('from', '')}"
        if kind == 'claim.refused':
            return f"refused: {data.get('resource', '')} held by {data.get('holder_task_id', '')}"
        if kind == 'inbox.acknowledged':
            return f"acknowledged {data.get('count', 0)} broadcast(s)"
        if kind == 'journal.logged':
            return f"logged progress on {tid}"
        if kind == 'desk.restart_announced':
            return f"announced a restart (~{data.get('seconds', 0)}s)"
        if kind == 'changelog.published':
            return f'published "{data.get("title", "")}"'
        if kind in ('project.created', 'project.updated', 'project.moved'):
            return kind.split('.')[1]
        if kind.startswith('refusal.'):
            return f"{kind.split('.')[1]} refusal {data.get('refusal_id', '')}"
        if kind == 'snooze.set':
            return f"snoozed {data.get('key', '')} until {data.get('until', '')}"
        if kind == 'snooze.clear':
            return f"cleared snooze {data.get('key', '')}"
        return f'{kind} {json.dumps(data)[:150]}'

    def events_page(self, project, before=None, limit=50):
        limit = max(1, min(int(limit), 200))
        with self.connection() as c:
            args = [project]
            query = 'SELECT * FROM events WHERE project=?'
            if before is not None:
                query += ' AND seq<?'
                args.append(int(before))
            query += ' ORDER BY seq DESC LIMIT ?'
            args.append(limit)
            rows = [dict(r) for r in c.execute(query, args)]
            for r in rows:
                r['data'] = json.loads(r['data'])
            task_ids = sorted({r['data'].get('task_id') for r in rows if r['data'].get('task_id')})
            titles = {}
            if task_ids:
                marks = ','.join('?' * len(task_ids))
                titles = {r['id']: r['title'] for r in c.execute(
                    f'SELECT id,title FROM tasks WHERE id IN ({marks})', task_ids)}
            names = self._names(c, [r['actor'] for r in rows])
            for r in rows:
                r['actor_name'] = names.get(r['actor'], r['actor'])
                r['text'] = self._event_text(r['kind'], r['data'], titles)
            return {'project': project, 'events': rows, 'next_before': rows[-1]['seq'] if rows else None}

    # ---- GET /api/lanes -----------------------------------------------------------

    def lanes(self, project, hours=12):
        hours = max(1, min(int(hours), 24 * 30))
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(hours=hours)
        start_iso, end_iso = start_dt.isoformat(), end_dt.isoformat()
        with self.connection() as c:
            tasks = [self.task(c, r['id']) for r in c.execute('SELECT id FROM tasks WHERE project=?', (project,))]
            task_ids = [t['id'] for t in tasks]
            starts, ends = {}, {}
            if task_ids:
                marks = ','.join('?' * len(task_ids))
                for r in c.execute(f"SELECT task_id, MIN(created) s FROM task_log WHERE task_id IN ({marks}) "
                                   "AND kind='claimed' GROUP BY task_id", task_ids):
                    starts[r['task_id']] = r['s']
                for r in c.execute(f"SELECT task_id, MAX(created) e FROM task_log WHERE task_id IN ({marks}) "
                                   "AND kind='done' GROUP BY task_id", task_ids):
                    ends[r['task_id']] = r['e']
            owners = {t['owner'] for t in tasks if t['owner']}
            session_rows = {}
            if owners:
                m = ','.join('?' * len(owners))
                for r in c.execute(f'SELECT id,name,kind,branch,last_seen FROM sessions WHERE id IN ({m})',
                                   list(owners)):
                    session_rows[r['id']] = dict(r)
            lanes = {}

            def lane(sid):
                if sid not in lanes:
                    row = session_rows.get(sid)
                    stale = True
                    if row:
                        stale = (datetime.now(timezone.utc) -
                                datetime.fromisoformat(row['last_seen'])).total_seconds() > 900
                    lanes[sid] = {'id': sid, 'name': row['name'] if row else sid,
                                 'kind': row['kind'] if row else '', 'branch': row['branch'] if row else '',
                                 'stale': stale, 'bars': [], 'marks': []}
                return lanes[sid]

            for t in tasks:
                if not t['owner'] or t['id'] not in starts:
                    continue
                bar_start = starts[t['id']]
                if t['status'] == 'DONE':
                    bar_end = ends.get(t['id'], t['updated'])
                    kind = 'done'
                else:
                    bar_end = end_iso
                    kind = 'blocked' if t['status'] == 'BLOCKED' else ('stale' if lane(t['owner'])['stale'] else 'run')
                if bar_end < start_iso or bar_start > end_iso:
                    continue
                lane(t['owner'])['bars'].append({'task_id': t['id'], 'title': t['title'], 'start': bar_start,
                                                 'end': bar_end, 'kind': kind})

            def mark_actor(sid):
                return sid not in (None, HUMAN, 'project-desk')

            for r in c.execute('SELECT * FROM deploys WHERE project=? AND created BETWEEN ? AND ?',
                               (project, start_iso, end_iso)):
                if mark_actor(r['session_id']):
                    lane(r['session_id'])['marks'].append({'t': r['created'], 'kind': 'deploy',
                        'text': f"deployed {r['service']} at {r['commit_ref']}", 'task_id': r['task_id']})
            for r in c.execute('''SELECT q.*, m.body FROM questions q JOIN messages m ON m.id=q.message_id
                                  WHERE q.project=? AND q.created BETWEEN ? AND ?''', (project, start_iso, end_iso)):
                if mark_actor(r['asker']):
                    lane(r['asker'])['marks'].append({'t': r['created'], 'kind': 'question',
                        'text': _first_line(r['body'], 120), 'task_id': r['task_id']})
            for r in c.execute('SELECT * FROM approvals WHERE project=? AND created BETWEEN ? AND ?',
                               (project, start_iso, end_iso)):
                if mark_actor(r['requester']):
                    lane(r['requester'])['marks'].append({'t': r['created'], 'kind': 'approval',
                        'text': r['title'], 'task_id': r['task_id']})
            for r in c.execute('SELECT * FROM refusals WHERE project=? AND created BETWEEN ? AND ?',
                               (project, start_iso, end_iso)):
                if mark_actor(r['session_id']):
                    lane(r['session_id'])['marks'].append({'t': r['created'], 'kind': 'refused',
                        'text': r['title'], 'task_id': r['holder_task_id']})
            for r in c.execute('SELECT * FROM handoff_briefs WHERE project=? AND created BETWEEN ? AND ?',
                               (project, start_iso, end_iso)):
                if mark_actor(r['source_session']):
                    lane(r['source_session'])['marks'].append({'t': r['created'], 'kind': 'handoff',
                        'text': f"offered to {self._display_name(c, r['target_session'])}", 'task_id': r['task_id']})
            for r in c.execute("SELECT * FROM notes WHERE project=? AND kind='decision' AND created BETWEEN ? AND ?",
                               (project, start_iso, end_iso)):
                if mark_actor(r['author']):
                    lane(r['author'])['marks'].append({'t': r['created'], 'kind': 'decision',
                        'text': _first_line(r['body'], 120), 'task_id': None})

            out_sessions = [v for v in lanes.values() if v['bars'] or v['marks']]
            for s in out_sessions:
                s['marks'].sort(key=lambda mk: mk['t'])
                s['bars'].sort(key=lambda b: b['start'])
            return {'start': start_iso, 'end': end_iso, 'sessions': out_sessions}
