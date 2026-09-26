"""Memory and coordination features layered on the Store.

Lessons (shared, path-scoped memory), the task journal, resuming a previous
session, inbox triage, questions and approvals, the resource queue and deploy
record, crossover contracts, structured evidence, stale-claim alerts and agent
reopen. PROTOCOL.md, "Memory and coordination", describes the rules.

Every table here is new; none of the original tables changes shape, so an
earlier release still runs against a database this one has opened.
"""
import json
import re
from datetime import datetime, timedelta, timezone

from deskcore import Conflict, HUMAN, ident, now, overlaps, scopes, text

FEATURE_SCHEMA = '''
    CREATE TABLE IF NOT EXISTS lessons (
        id TEXT PRIMARY KEY, project TEXT NOT NULL, author TEXT NOT NULL, body TEXT NOT NULL,
        paths TEXT NOT NULL DEFAULT '[]', tags TEXT NOT NULL DEFAULT '[]',
        created TEXT NOT NULL, updated TEXT NOT NULL,
        archived INTEGER NOT NULL DEFAULT 0, archive_reason TEXT NOT NULL DEFAULT '');
    CREATE VIRTUAL TABLE IF NOT EXISTS lessons_fts USING fts5(lesson_id UNINDEXED, body, tags, paths);
    CREATE TABLE IF NOT EXISTS task_log (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, project TEXT NOT NULL,
        author TEXT NOT NULL, kind TEXT NOT NULL, entry TEXT NOT NULL, created TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS task_log_task ON task_log(task_id, seq);
    CREATE TABLE IF NOT EXISTS session_links (
        old_session TEXT PRIMARY KEY, new_session TEXT NOT NULL, created TEXT NOT NULL, tasks TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS message_meta (
        message_id TEXT PRIMARY KEY, kind TEXT NOT NULL, reply_to TEXT);
    CREATE TABLE IF NOT EXISTS questions (
        message_id TEXT PRIMARY KEY, project TEXT NOT NULL, asker TEXT NOT NULL, recipient TEXT NOT NULL,
        task_id TEXT, status TEXT NOT NULL DEFAULT 'open', answer_message_id TEXT, answered_by TEXT,
        created TEXT NOT NULL, answered TEXT);
    CREATE TABLE IF NOT EXISTS approvals (
        id TEXT PRIMARY KEY, project TEXT NOT NULL, requester TEXT NOT NULL, task_id TEXT,
        title TEXT NOT NULL, options TEXT NOT NULL, context TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', decision TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, decided TEXT);
    CREATE TABLE IF NOT EXISTS resource_queue (
        resource TEXT NOT NULL, session_id TEXT NOT NULL, project TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, notified TEXT,
        PRIMARY KEY(resource, session_id));
    CREATE TABLE IF NOT EXISTS deploys (
        id TEXT PRIMARY KEY, project TEXT NOT NULL, service TEXT NOT NULL, commit_ref TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL, task_id TEXT, created TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS deploys_service ON deploys(service, created);
    CREATE TABLE IF NOT EXISTS crossover_contracts (
        crossover_id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
        author TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(crossover_id, version));
    CREATE TABLE IF NOT EXISTS evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, project TEXT NOT NULL,
        author TEXT NOT NULL, context TEXT NOT NULL, command TEXT NOT NULL DEFAULT '',
        exit_code INTEGER, tests_passed INTEGER, tests_failed INTEGER,
        commit_ref TEXT NOT NULL DEFAULT '', output TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS evidence_task ON evidence(task_id);
    CREATE TABLE IF NOT EXISTS stale_alerts (
        task_id TEXT NOT NULL, owner TEXT NOT NULL, owner_last_seen TEXT NOT NULL, alerted TEXT NOT NULL,
        PRIMARY KEY(task_id, owner, owner_last_seen));
'''

MESSAGE_KINDS = ('message', 'question', 'answer', 'deploy', 'fyi', 'update', 'handoff',
                 'decision', 'crossover', 'stale', 'queue')
GLOBAL = '*'                  # a lesson every project can recall
SYSTEM = 'project-desk'       # sender of the desk's own notices
RESUME_AFTER_MINUTES = 30     # a session must be this quiet before another may resume it
QUEUE_TURN_MINUTES = 15       # the head of a queue has this long to claim before the next is told
STALE_HOURS = 6
EVIDENCE_FIELDS = ('command', 'exit_code', 'tests_passed', 'tests_failed', 'commit', 'output')

_HEADER = re.compile(r'\s*([A-Z][A-Z0-9 /&+\-]{2,60}?)\s*[:(]')


def infer_kind(body):
    """A kind for a message sent without one, from the SHOUTED header agents already use."""
    match = _HEADER.match(body or '')
    if not match:
        return 'message'
    head = match.group(1)
    if 'DEPLOY' in head or 'MIGRATION' in head or 'RELEASE' in head:
        return 'deploy'
    if 'CROSSOVER' in head or 'SIGN-OFF' in head or 'CONTRACT' in head:
        return 'crossover'
    if 'QUESTION' in head:
        return 'question'
    if head.startswith(('HEADS-UP', 'FYI', 'ANNOUNCEMENT', 'SUPERVISOR', 'RULE', 'NOTE')):
        return 'fyi'
    return 'message'


def _ago(minutes=0, hours=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes, hours=hours)).isoformat()


def _first_line(body, limit=160):
    line = next((part.strip() for part in (body or '').splitlines() if part.strip()), '')
    return line[:limit] + ('…' if len(line) > limit else '')


def _int(value, label):
    if value is None or value == '':
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ValueError(f'{label} must be a whole number')
    return value


class FeaturesMixin:
    LESSON_CHARS = 2000

    # ---- shared helpers ---------------------------------------------------

    def _migrate_features(self, c):
        c.executescript(FEATURE_SCHEMA)
        columns = {r[1] for r in c.execute('PRAGMA table_info(crossover_members)')}
        if 'signed_version' not in columns:
            c.execute('ALTER TABLE crossover_members ADD COLUMN signed_version INTEGER')

    def _display_name(self, c, session_id):
        if session_id == HUMAN:
            return 'Human'
        if session_id == SYSTEM:
            return 'Project Desk'
        row = c.execute('SELECT name FROM sessions WHERE id=?', (session_id,)).fetchone()
        return row['name'] if row else session_id

    def _names(self, c, session_ids):
        """Display names for many sessions in one query (the snapshot must not look them up row by row)."""
        ids = sorted({sid for sid in session_ids if sid and sid not in (HUMAN, SYSTEM)})
        names = {HUMAN: 'Human', SYSTEM: 'Project Desk'}
        for chunk in range(0, len(ids), 500):
            part = ids[chunk:chunk + 500]
            names.update({r['id']: r['name'] for r in c.execute(
                f"SELECT id,name FROM sessions WHERE id IN ({','.join('?' * len(part))})", part)})
        return names

    def _recent_logs(self, c, task_ids, per_task=2, chars=200):
        """The last few journal entries for many tasks, in one query."""
        if not task_ids:
            return {}
        rows = c.execute(f'''SELECT task_id,seq,author,kind,entry,created FROM (
                                SELECT *, ROW_NUMBER() OVER (PARTITION BY task_id ORDER BY seq DESC) n
                                FROM task_log WHERE task_id IN ({','.join('?' * len(task_ids))}))
                             WHERE n<=? ORDER BY task_id, seq''', (*task_ids, per_task)).fetchall()
        names = self._names(c, [r['author'] for r in rows])
        out = {}
        for r in rows:
            out.setdefault(r['task_id'], []).append(
                {'seq': r['seq'], 'author': r['author'], 'author_name': names.get(r['author'], r['author']),
                 'kind': r['kind'], 'entry': r['entry'][:chars], 'created': r['created']})
        return out

    def _board_lessons(self, c, project, limit=40):
        rows = c.execute('SELECT * FROM lessons WHERE archived=0 AND project IN (?,?) ORDER BY updated DESC LIMIT ?',
                         (project, GLOBAL, limit)).fetchall()
        names = self._names(c, [r['author'] for r in rows])
        return [{'id': r['id'], 'body': r['body'][:400], 'paths': json.loads(r['paths']), 'tags': json.loads(r['tags']),
                 'scope': 'global' if r['project'] == GLOBAL else 'project',
                 'author_name': names.get(r['author'], r['author']), 'updated': r['updated']} for r in rows]

    def _board_approvals(self, c, project, limit=30):
        rows = c.execute("SELECT * FROM approvals WHERE project=? AND (status='pending' OR decided>?) "
                         'ORDER BY created DESC LIMIT ?', (project, _ago(hours=72), limit)).fetchall()
        names = self._names(c, [r['requester'] for r in rows])
        return [{**dict(r), 'options': json.loads(r['options']),
                 'requester_name': names.get(r['requester'], r['requester'])} for r in rows]

    def _aliases(self, c, session_id):
        """This session plus every earlier session it resumed (resume_session)."""
        ids, frontier = [session_id], [session_id]
        while frontier and len(ids) < 50:
            marks = ','.join('?' * len(frontier))
            found = [r[0] for r in c.execute(
                f'SELECT old_session FROM session_links WHERE new_session IN ({marks})', frontier)]
            frontier = [sid for sid in found if sid not in ids]
            ids.extend(frontier)
        return ids

    def _unread_rows(self, c, s, ids, limit=200):
        marks = ','.join('?' * len(ids))
        return [dict(r) for r in c.execute(
            f'''SELECT m.* FROM messages m WHERE project=?
                AND recipient IN ('all',?,{marks}) AND sender NOT IN ({marks}) AND NOT EXISTS
                (SELECT 1 FROM receipts r WHERE r.message_id=m.id AND r.session_id IN ({marks}))
                ORDER BY created LIMIT ?''', (s['project'], s['kind'], *ids, *ids, *ids, limit))]

    def _log(self, c, task_id, project, author, kind, entry):
        c.execute('INSERT INTO task_log(task_id,project,author,kind,entry,created) VALUES(?,?,?,?,?,?)',
                  (task_id, project, author, kind, str(entry)[:4000], now()))

    def _journal(self, c, task_id, limit=50):
        rows = [dict(r) for r in c.execute(
            'SELECT seq,author,kind,entry,created FROM task_log WHERE task_id=? ORDER BY seq DESC LIMIT ?',
            (task_id, limit))]
        for row in rows:
            row['author_name'] = self._display_name(c, row['author'])
        return list(reversed(rows))

    # ---- 1. lessons: shared memory, delivered by path ----------------------

    def _tags(self, tags):
        if not tags:
            return []
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, list) or len(tags) > 10:
            raise ValueError('Give up to 10 tags')
        out = []
        for tag in tags:
            tag = text(tag, 'tag', 40).lower()
            if not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', tag):
                raise ValueError(f'Tags are short lowercase words: {tag!r}')
            out.append(tag)
        return sorted(set(out))

    def _lesson_row(self, c, row, compact=False):
        item = dict(row)
        item['paths'] = json.loads(item['paths'])
        item['tags'] = json.loads(item['tags'])
        item['scope'] = 'global' if item['project'] == GLOBAL else 'project'
        item['author_name'] = self._display_name(c, item['author'])
        if compact:
            return {k: item[k] for k in ('id', 'body', 'paths', 'tags', 'scope', 'author_name', 'updated')}
        return item

    def _remember(self, c, project, author, body, paths=None, tags=None, scope='project'):
        body = text(body, 'lesson', self.LESSON_CHARS)
        paths = scopes(paths) if paths else []
        tags = self._tags(tags)
        if scope not in ('project', 'global'):
            raise ValueError("scope is 'project' or 'global'")
        target = GLOBAL if scope == 'global' else project
        duplicate = c.execute('SELECT * FROM lessons WHERE project=? AND body=? AND archived=0',
                              (target, body)).fetchone()
        if duplicate:
            return {**self._lesson_row(c, duplicate), 'duplicate': True}
        lesson_id, stamp = ident('l-'), now()
        c.execute('INSERT INTO lessons(id,project,author,body,paths,tags,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                  (lesson_id, target, author, body, json.dumps(paths), json.dumps(tags), stamp, stamp))
        c.execute('INSERT INTO lessons_fts(lesson_id,body,tags,paths) VALUES(?,?,?,?)',
                  (lesson_id, body, ' '.join(tags), ' '.join(paths)))
        self.event(c, project, author, 'lesson.created', {'lesson_id': lesson_id, 'paths': paths,
                                                          'tags': tags, 'scope': scope})
        return self._lesson_row(c, c.execute('SELECT * FROM lessons WHERE id=?', (lesson_id,)).fetchone())

    def remember(self, key, body, paths=None, tags=None, scope='project'):
        with self.connection(True) as c:
            s = self.auth(c, key)
            return self._remember(c, s['project'], s['id'], body, paths, tags, scope)

    @staticmethod
    def _fts_query(query, joiner=' '):
        words = re.findall(r'[\w./-]+', query or '')[:12]
        return joiner.join('"' + word.replace('"', '') + '"' for word in words)

    def _recall(self, c, project, query='', paths=None, tags=None, limit=10):
        limit = max(1, min(_int(limit, 'limit') or 10, 50))
        paths = scopes(paths) if paths else []
        tags = self._tags(tags)
        if query and query.strip():
            ids = []
            for joiner in (' ', ' OR '):   # every word first, then any word
                expression = self._fts_query(query, joiner)
                if expression:
                    ids = [r[0] for r in c.execute(
                        'SELECT lesson_id FROM lessons_fts WHERE lessons_fts MATCH ? ORDER BY rank LIMIT 200',
                        (expression,))]
                if ids:
                    break
            order = {lesson_id: n for n, lesson_id in enumerate(ids)}
            rows = [r for r in c.execute(
                f"SELECT * FROM lessons WHERE id IN ({','.join('?' * len(ids))}) AND archived=0 AND project IN (?,?)",
                (*ids, project, GLOBAL))] if ids else []
            rows.sort(key=lambda r: order[r['id']])
        else:
            rows = c.execute('SELECT * FROM lessons WHERE archived=0 AND project IN (?,?) ORDER BY updated DESC LIMIT 500',
                             (project, GLOBAL)).fetchall()
        out = []
        for row in rows:
            lesson_paths, lesson_tags = json.loads(row['paths']), json.loads(row['tags'])
            if paths and not any(overlaps(a, b) for a in paths for b in lesson_paths if not a.startswith('service:')):
                continue
            if tags and not set(tags) & set(lesson_tags):
                continue
            out.append(self._lesson_row(c, row))
            if len(out) >= limit:
                break
        return out

    def recall(self, key, query='', paths=None, tags=None, limit=10):
        with self.connection() as c:
            s = self.auth(c, key)
            return {'project': s['project'], 'lessons': self._recall(c, s['project'], query, paths, tags, limit)}

    def _lessons_for_paths(self, c, project, resources, limit=5):
        """Lessons whose paths overlap these resources, most specific first."""
        wanted = [r for r in resources if not r.startswith('service:')]
        if not wanted:
            return []
        scored = []
        for row in c.execute('SELECT * FROM lessons WHERE archived=0 AND project IN (?,?) AND paths!=?',
                             (project, GLOBAL, '[]')):
            hits = [p for p in json.loads(row['paths']) for r in wanted if overlaps(p, r)]
            if hits:
                scored.append((max(len(p) for p in hits), row['updated'], row))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [self._lesson_row(c, row, compact=True) for _, _, row in scored[:limit]]

    def _forget(self, c, project, actor, lesson_id, reason):
        reason = text(reason, 'reason', 500)
        row = c.execute('SELECT * FROM lessons WHERE id=?', (lesson_id,)).fetchone()
        if not row or row['project'] not in (project, GLOBAL):
            raise ValueError('Unknown lesson in this project')
        if row['archived']:
            return {'lesson_id': lesson_id, 'archived': True}
        c.execute('UPDATE lessons SET archived=1,archive_reason=?,updated=? WHERE id=?', (reason, now(), lesson_id))
        c.execute('DELETE FROM lessons_fts WHERE lesson_id=?', (lesson_id,))
        self.event(c, project, actor, 'lesson.archived', {'lesson_id': lesson_id, 'reason': reason})
        return {'lesson_id': lesson_id, 'archived': True}

    def forget(self, key, lesson_id, reason):
        with self.connection(True) as c:
            s = self.auth(c, key)
            return self._forget(c, s['project'], s['id'], lesson_id, reason)

    # ---- 2. the task journal -----------------------------------------------

    def log_progress(self, key, task_id, entry):
        entry = text(entry, 'progress entry', 4000)
        with self.connection(True) as c:
            s = self.auth(c, key)
            t = self.task(c, task_id)
            if t['owner'] != s['id']:
                raise PermissionError('Only the task owner writes its log; message the owner instead')
            self._log(c, task_id, t['project'], s['id'], 'progress', entry)
            # Not 'task.*': a progress note must not ring every dashboard bell.
            self.event(c, t['project'], s['id'], 'journal.logged', {'task_id': task_id})
            return {'task_id': task_id, 'logged': True, 'recent': self._journal(c, task_id, 5)}

    # ---- 3. resume a previous session of yours -------------------------------

    def _resumable(self, c, s):
        out = []
        for row in c.execute('''SELECT id,name,branch,last_seen FROM sessions WHERE project=? AND kind=?
                                AND worktree=? AND branch=? AND imported=0 AND id!=? AND last_seen<?
                                AND id NOT IN (SELECT old_session FROM session_links)
                                ORDER BY last_seen DESC LIMIT 20''',
                             (s['project'], s['kind'], s['worktree'], s['branch'], s['id'],
                              _ago(minutes=RESUME_AFTER_MINUTES))):
            tasks = [{'id': t['id'], 'title': t['title'], 'status': t['status']} for t in c.execute(
                "SELECT id,title,status FROM tasks WHERE owner=? AND status!='DONE' ORDER BY updated DESC", (row['id'],))]
            if tasks:
                out.append({'session_id': row['id'], 'name': row['name'], 'branch': row['branch'],
                            'last_seen': row['last_seen'], 'tasks': tasks})
        return out[:5]

    def resume_session(self, key, from_session, reason=''):
        reason = (reason or '').strip()[:1000]
        with self.connection(True) as c:
            s = self.auth(c, key)
            old = c.execute('SELECT * FROM sessions WHERE id=?', (from_session,)).fetchone()
            if not old or old['imported'] or old['id'] == s['id']:
                raise ValueError('Name an earlier session of yours (see register_session → resumable)')
            if (old['project'], old['kind'], old['worktree'], old['branch']) != \
                    (s['project'], s['kind'], s['worktree'], s['branch']):
                raise PermissionError('Resume only an earlier session from your own project, agent kind, worktree and branch')
            if old['last_seen'] >= _ago(minutes=RESUME_AFTER_MINUTES):
                raise Conflict(f'{from_session} checked in less than {RESUME_AFTER_MINUTES} min ago and may still be '
                               'running; ask it for a handoff instead')
            link = c.execute('SELECT new_session FROM session_links WHERE old_session=?', (from_session,)).fetchone()
            if link:
                raise Conflict(f'{from_session} was already resumed by {link["new_session"]}')
            stamp = now()
            tasks = [r[0] for r in c.execute("SELECT id FROM tasks WHERE owner=? AND status!='DONE'", (from_session,))]
            note = f"Resumed from {from_session} ({old['name']}) by {s['id']} ({s['name']})" + (f': {reason}' if reason else '')
            for task_id in tasks:
                c.execute('UPDATE tasks SET owner=?,version=version+1,updated=? WHERE id=?', (s['id'], stamp, task_id))
                self._log(c, task_id, s['project'], s['id'], 'resumed', note)
            c.execute('UPDATE tasks SET pending_owner=? WHERE pending_owner=?', (s['id'], from_session))
            c.execute("UPDATE handoff_briefs SET target_session=? WHERE target_session=? AND status='offered'",
                      (s['id'], from_session))
            c.execute('''UPDATE OR IGNORE resource_queue SET session_id=? WHERE session_id=?''', (s['id'], from_session))
            c.execute('DELETE FROM resource_queue WHERE session_id=?', (from_session,))
            c.execute("UPDATE questions SET asker=? WHERE asker=? AND status='open'", (s['id'], from_session))
            c.execute("UPDATE approvals SET requester=? WHERE requester=? AND status='pending'", (s['id'], from_session))
            c.execute('INSERT INTO session_links VALUES(?,?,?,?)', (from_session, s['id'], stamp, json.dumps(tasks)))
            self.event(c, s['project'], s['id'], 'session.resumed', {'from': from_session, 'tasks': tasks})
            self._message(c, s['project'], s['id'], from_session,
                          f"RESUMED: {s['name']} ({s['id']}) took over your open tasks "
                          f"{', '.join(tasks) or '(none)'} after {RESUME_AFTER_MINUTES}+ min of silence"
                          + (f': {reason}' if reason else '') +
                          '. If you are still running, check in and ask it for a handoff.', None, kind='fyi')
            # Many sessions can share one checkout, so the human sees every resume and can Reassign it back.
            self._message(c, s['project'], s['id'], HUMAN,
                          f"RESUMED: {s['name']} ({s['id']}) took over {old['name']} ({from_session})'s open tasks "
                          f"{', '.join(tasks) or '(none)'}" + (f': {reason}' if reason else '') +
                          '. Reassign on the dashboard if that was not its own earlier session.', None, kind='fyi')
            return {'resumed_from': from_session, 'resumed_name': old['name'],
                    'tasks': [{**self.task(c, task_id), 'journal': self._journal(c, task_id, 10)} for task_id in tasks],
                    'inbox': 'Messages addressed to the earlier session now reach your inbox too.'}

    # ---- 4. inbox triage -----------------------------------------------------

    def _inbox_digest(self, c, s, ids, rows):
        out = []
        for m in self._decorate(c, rows):
            if m['recipient'] in ids:
                to = 'you'
            elif m['recipient'] == s['kind']:
                to = f"all {s['kind']}"
            else:
                to = 'all'
            item = {'id': m['id'], 'sender': m['sender'],
                    'sender_name': m.get('from_name') or self._display_name(c, m['sender']),
                    'to': to, 'kind': m.get('kind') or infer_kind(m['body']), 'task_id': m['task_id'],
                    'created': m['created'], 'first_line': _first_line(m['body']), 'chars': len(m['body'])}
            for field in ('from_project', 'crossover_id', 'question_status', 'reply_to'):
                if m.get(field):
                    item[field] = m[field]
            out.append(item)
        out.sort(key=lambda i: (i['to'] != 'you', i['kind'] != 'question', i['created']))
        return out

    def read_messages(self, key, message_ids):
        if isinstance(message_ids, str):
            message_ids = [message_ids]
        if not isinstance(message_ids, list) or not message_ids or len(message_ids) > 50:
            raise ValueError('Name 1–50 message ids')
        with self.connection() as c:
            s = self.auth(c, key)
            ids = self._aliases(c, s['id'])
            found, refused = [], []
            for mid in dict.fromkeys(message_ids):
                row = c.execute('SELECT * FROM messages WHERE id=?', (mid,)).fetchone()
                if row and row['project'] == s['project'] and (
                        row['recipient'] in ('all', s['kind']) or row['recipient'] in ids or row['sender'] in ids):
                    found.append(dict(row))
                else:
                    refused.append(mid)
            return {'messages': self._decorate(c, found), 'refused': refused,
                    'note': 'Reading does not acknowledge; call acknowledge_message when done.'}

    def acknowledge_inbox(self, key, kinds=None, include_direct=False):
        if kinds is not None:
            kinds = [kinds] if isinstance(kinds, str) else list(kinds)
            unknown = sorted(set(kinds) - set(MESSAGE_KINDS))
            if unknown:
                raise ValueError(f"Unknown kind(s) {', '.join(unknown)}; kinds: {', '.join(MESSAGE_KINDS)}")
        with self.connection(True) as c:
            s = self.auth(c, key)
            ids = self._aliases(c, s['id'])
            chosen, kept = [], 0
            for m in self._inbox_digest(c, s, ids, self._unread_rows(c, s, ids, 2000)):
                if (m['to'] == 'you' and not include_direct) or (kinds and m['kind'] not in kinds):
                    kept += 1
                    continue
                chosen.append(m['id'])
            stamp = now()
            c.executemany('INSERT OR IGNORE INTO receipts VALUES(?,?,?)', [(mid, s['id'], stamp) for mid in chosen])
            if chosen:
                self.event(c, s['project'], s['id'], 'inbox.acknowledged', {'count': len(chosen), 'kinds': kinds})
            return {'acknowledged': len(chosen), 'message_ids': chosen, 'left_unread': kept,
                    'note': 'Messages addressed to you stay unread unless include_direct is true.'}

    # ---- 5. questions and approvals ------------------------------------------

    def ask(self, key, recipient, question, task_id=None):
        with self.connection(True) as c:
            s = self.auth(c, key)
            if isinstance(recipient, str) and recipient.startswith('x-'):
                raise ValueError('Ask one party: a session, a project or the human, not a whole crossover')
            sent = self._message(c, s['project'], s['id'], recipient, question, task_id, kind='question')
            target = sent.get('to_project', s['project'])
            row = c.execute('SELECT recipient FROM messages WHERE id=?', (sent['message_id'],)).fetchone()
            c.execute('INSERT INTO questions(message_id,project,asker,recipient,task_id,created) VALUES(?,?,?,?,?,?)',
                      (sent['message_id'], target, s['id'], row['recipient'], task_id, now()))
            return {**sent, 'question_id': sent['message_id'], 'status': 'open',
                    'answered_by': 'the recipient replies with send_message(reply_to="%s")' % sent['message_id']}

    def _answer(self, c, project, sender, reply_to, answer_id):
        question = c.execute("SELECT * FROM questions WHERE message_id=? AND status='open'", (reply_to,)).fetchone()
        if not question or question['project'] != project:
            return
        if sender == HUMAN:
            addressed = question['recipient'] in (HUMAN, 'all')
        else:
            me = c.execute('SELECT kind FROM sessions WHERE id=?', (sender,)).fetchone()
            addressed = question['recipient'] in ('all', me['kind'] if me else '') or \
                question['recipient'] in self._aliases(c, sender)
        if addressed and question['asker'] != sender:
            c.execute("UPDATE questions SET status='answered',answer_message_id=?,answered_by=?,answered=? WHERE message_id=?",
                      (answer_id, sender, now(), reply_to))

    def _questions(self, c, s, ids):
        marks = ','.join('?' * len(ids))
        for_me = [dict(r) for r in c.execute(
            f'''SELECT q.message_id,q.asker,q.task_id,q.created,m.body FROM questions q JOIN messages m ON m.id=q.message_id
                WHERE q.project=? AND q.status='open' AND q.asker NOT IN ({marks})
                AND q.recipient IN ('all',?,{marks}) ORDER BY q.created LIMIT 50''',
            (s['project'], *ids, s['kind'], *ids))]
        mine = [dict(r) for r in c.execute(
            f'''SELECT q.message_id,q.recipient,q.project,q.status,q.answer_message_id,q.answered_by,q.created,q.answered
                FROM questions q WHERE q.asker IN ({marks}) ORDER BY q.created DESC LIMIT 20''', ids)]
        for item in for_me:
            item['asker_name'] = self._display_name(c, item['asker'])
            item['question'] = _first_line(item.pop('body'), 300)
        for item in mine:
            if item['answer_message_id']:
                answer = c.execute('SELECT body FROM messages WHERE id=?', (item['answer_message_id'],)).fetchone()
                item['answer'] = _first_line(answer['body'], 300) if answer else ''
        return {'for_me': for_me, 'mine': mine}

    def request_approval(self, key, title, options, context, task_id=None):
        title = text(title, 'title', 200)
        context = text(context, 'context', 6000)
        if not isinstance(options, list) or not 2 <= len(options) <= 6:
            raise ValueError('Offer 2–6 options')
        options = [text(o, 'option', 200) for o in options]
        if len(set(options)) != len(options):
            raise ValueError('Options must differ')
        with self.connection(True) as c:
            s = self.auth(c, key)
            if task_id and self.task(c, task_id)['project'] != s['project']:
                raise ValueError('Task belongs to another project')
            approval_id = ident('a-')
            c.execute('''INSERT INTO approvals(id,project,requester,task_id,title,options,context,created)
                         VALUES(?,?,?,?,?,?,?,?)''',
                      (approval_id, s['project'], s['id'], task_id, title, json.dumps(options), context, now()))
            body = (f"APPROVAL NEEDED {approval_id} from {s['name']} ({s['id']}): {title}\n{context}\nOptions:\n"
                    + '\n'.join(f'  {n}. {option}' for n, option in enumerate(options, 1))
                    + '\nDecide on the dashboard under "Decisions waiting for you".')
            self._message(c, s['project'], s['id'], HUMAN, body, task_id, kind='decision')
            self.event(c, s['project'], s['id'], 'approval.requested', {'approval_id': approval_id, 'title': title})
            return self._approval_row(c, approval_id)

    def _approval_row(self, c, approval_id):
        row = c.execute('SELECT * FROM approvals WHERE id=?', (approval_id,)).fetchone()
        if not row:
            raise ValueError('Unknown approval')
        item = dict(row)
        item['options'] = json.loads(item['options'])
        item['requester_name'] = self._display_name(c, item['requester'])
        return item

    def _decide_approval(self, c, project, data):
        approval = self._approval_row(c, data.get('approval_id', ''))
        if approval['project'] != project:
            raise ValueError('Approval belongs to another project')
        if approval['status'] != 'pending':
            raise Conflict('Already decided')
        decision = text(data.get('decision', ''), 'decision', 200)
        note = (data.get('note') or '').strip()[:2000]
        if decision not in approval['options'] and not note:
            raise ValueError('Choose one of the options, or explain another answer in the note')
        c.execute("UPDATE approvals SET status='decided',decision=?,note=?,decided=? WHERE id=?",
                  (decision, note, now(), approval['id']))
        summary = f"{approval['id']} ({approval['title']}) for {approval['requester_name']}: {decision}" + \
                  (f' — {note}' if note else '')
        self._note(c, project, HUMAN, 'Decision on ' + summary, 'decision')
        self._message(c, project, HUMAN, approval['requester'], 'DECISION ' + summary, approval['task_id'],
                      kind='decision')
        self.event(c, project, HUMAN, 'approval.decided', {'approval_id': approval['id'], 'decision': decision})
        return self._approval_row(c, approval['id'])

    # ---- 7. the resource queue and the deploy record ---------------------------

    def _holders(self, c, project, resource, exclude_owner=None):
        rows = c.execute("SELECT * FROM claims WHERE (project=? OR resource LIKE 'service:%')", (project,))
        out = []
        for row in rows:
            if overlaps(resource, row['resource']):
                task = c.execute('SELECT owner,title FROM tasks WHERE id=?', (row['task_id'],)).fetchone()
                if exclude_owner and task and task['owner'] == exclude_owner:
                    continue
                out.append({'resource': row['resource'], 'task_id': row['task_id'],
                            'owner': task['owner'] if task else None, 'title': task['title'] if task else ''})
        return out

    def _queue_view(self, c, resource, project):
        rows = c.execute('''SELECT * FROM resource_queue WHERE resource=? AND (resource LIKE 'service:%' OR project=?)
                            ORDER BY created''', (resource, project)).fetchall()
        return [{'position': n, 'session_id': r['session_id'], 'name': self._display_name(c, r['session_id']),
                 'note': r['note'], 'since': r['created'], 'told_its_turn': r['notified']}
                for n, r in enumerate(rows, 1)]

    def _notify_queues(self, c):
        """Tell the head of each queue when its resource is free; skip a head that let its turn lapse."""
        groups = c.execute('SELECT DISTINCT resource, project FROM resource_queue').fetchall()
        for group in groups:
            resource, project = group['resource'], group['project']
            while True:
                head = c.execute('''SELECT * FROM resource_queue WHERE resource=? AND (resource LIKE 'service:%' OR project=?)
                                    ORDER BY created LIMIT 1''', (resource, project)).fetchone()
                if not head:
                    break
                if self._holders(c, head['project'], resource, exclude_owner=head['session_id']):
                    if head['notified']:   # someone else took it; tell the head again when it frees
                        c.execute('UPDATE resource_queue SET notified=NULL WHERE resource=? AND session_id=?',
                                  (resource, head['session_id']))
                    break
                if not head['notified']:
                    c.execute('UPDATE resource_queue SET notified=? WHERE resource=? AND session_id=?',
                              (now(), resource, head['session_id']))
                    self._message(c, head['project'], SYSTEM, head['session_id'],
                                  f'YOUR TURN: {resource} is free and you are first in its queue. Claim it now with '
                                  f'claim_task(resources=["{resource}"], ...); after {QUEUE_TURN_MINUTES} min the next '
                                  'in line is told.', None, kind='queue')
                    break
                if head['notified'] < _ago(minutes=QUEUE_TURN_MINUTES):
                    c.execute('DELETE FROM resource_queue WHERE resource=? AND session_id=?', (resource, head['session_id']))
                    self._message(c, head['project'], SYSTEM, head['session_id'],
                                  f'QUEUE: your turn for {resource} lapsed after {QUEUE_TURN_MINUTES} min; you left the '
                                  'queue. Join again with queue_for when you are ready.', None, kind='queue')
                    continue
                break

    def tend_queues(self):
        with self.connection(True) as c:
            self._notify_queues(c)

    def queue_for(self, key, resource, note='', leave=False):
        resource = scopes([resource])[0]
        note = (note or '').strip()[:500]
        with self.connection(True) as c:
            s = self.auth(c, key)
            if leave:
                c.execute('DELETE FROM resource_queue WHERE resource=? AND session_id=?', (resource, s['id']))
                self._notify_queues(c)
                return {'resource': resource, 'left': True, 'queue': self._queue_view(c, resource, s['project'])}
            holders = self._holders(c, s['project'], resource, exclude_owner=s['id'])
            c.execute('INSERT OR IGNORE INTO resource_queue(resource,session_id,project,note,created) VALUES(?,?,?,?,?)',
                      (resource, s['id'], s['project'], note, now()))
            self.event(c, s['project'], s['id'], 'queue.joined', {'resource': resource})
            self._notify_queues(c)
            queue = self._queue_view(c, resource, s['project'])
            position = next((q['position'] for q in queue if q['session_id'] == s['id']), None)
            return {'resource': resource, 'position': position, 'held_by': holders, 'queue': queue,
                    'free_now': not holders,
                    'next': 'You will get a YOUR TURN message when it frees; wait_for(resources=[...]) blocks until then.'}

    def _drop_from_queues(self, c, session_id, resources):
        for row in c.execute('SELECT resource FROM resource_queue WHERE session_id=?', (session_id,)).fetchall():
            if any(overlaps(row['resource'], r) for r in resources):
                c.execute('DELETE FROM resource_queue WHERE resource=? AND session_id=?', (row['resource'], session_id))

    def _record_deploy(self, c, project, session_id, service, commit_ref, summary='', task_id=None):
        service = text(service, 'service', 100)
        service = service[len('service:'):] if service.startswith('service:') else service
        if not re.fullmatch(r'[a-zA-Z0-9_.:-]+', service):
            raise ValueError('Name the service like web, api or worker')
        deploy_id = ident('d-')
        c.execute('INSERT INTO deploys VALUES(?,?,?,?,?,?,?,?)',
                  (deploy_id, project, service, text(commit_ref, 'commit', 200), (summary or '')[:1000],
                   session_id, task_id, now()))
        self.event(c, project, session_id, 'deploy.recorded', {'deploy_id': deploy_id, 'service': service,
                                                               'commit_ref': commit_ref})
        return deploy_id

    def record_deploy(self, key, service, commit_ref, summary='', task_id=None):
        with self.connection(True) as c:
            s = self.auth(c, key)
            if task_id and self.task(c, task_id)['project'] != s['project']:
                raise ValueError('Task belongs to another project')
            deploy_id = self._record_deploy(c, s['project'], s['id'], service, commit_ref, summary, task_id)
            return {'deploy_id': deploy_id, 'prod': self._prod(c)}

    def _prod(self, c, service=''):
        rows = c.execute('''SELECT d.* FROM deploys d JOIN (SELECT service, MAX(created) latest FROM deploys GROUP BY service) x
                            ON d.service=x.service AND d.created=x.latest ORDER BY d.service''').fetchall()
        latest = [{**dict(r), 'by': self._display_name(c, r['session_id'])} for r in rows]
        out = {'services': latest}
        if service:
            service = service[len('service:'):] if service.startswith('service:') else service
            out['history'] = [{**dict(r), 'by': self._display_name(c, r['session_id'])} for r in c.execute(
                'SELECT * FROM deploys WHERE service=? ORDER BY created DESC LIMIT 10', (service,))]
        return out

    def prod_state(self, key, service=''):
        with self.connection() as c:
            self.auth(c, key)
            return self._prod(c, service)

    # ---- 8. crossover contracts ----------------------------------------------

    def _contract_version(self, c, crossover_id):
        return c.execute('SELECT MAX(version) FROM crossover_contracts WHERE crossover_id=?',
                         (crossover_id,)).fetchone()[0]

    def crossover_contract(self, key, crossover_id, body=None):
        with self.connection(True) as c:
            s = self.auth(c, key)
            self._crossover_row(c, crossover_id)
            member = c.execute('SELECT * FROM crossover_members WHERE crossover_id=? AND project=?',
                               (crossover_id, s['project'])).fetchone()
            if not member:
                raise PermissionError(f"Project {s['project']} is not part of crossover {crossover_id}")
            if body is None:
                row = c.execute('SELECT * FROM crossover_contracts WHERE crossover_id=? ORDER BY version DESC LIMIT 1',
                                (crossover_id,)).fetchone()
                return dict(row) if row else {'crossover_id': crossover_id, 'version': 0, 'body': ''}
            if not member['task_id']:
                raise PermissionError('Join the crossover before setting its contract')
            body = text(body, 'contract', 50000)
            version = (self._contract_version(c, crossover_id) or 0) + 1
            stamp = now()
            c.execute('INSERT INTO crossover_contracts VALUES(?,?,?,?,?)', (crossover_id, version, body, s['id'], stamp))
            c.execute('''UPDATE crossover_members SET signoff='',signed_by=NULL,signed=NULL,signed_version=NULL
                         WHERE crossover_id=?''', (crossover_id,))
            c.execute('UPDATE crossovers SET updated=? WHERE id=?', (stamp, crossover_id))
            self.event(c, s['project'], s['id'], 'crossover.contract', {'crossover_id': crossover_id, 'version': version})
            self._crossover_notice(c, crossover_id, s,
                f"CONTRACT v{version} for {crossover_id} set by {s['name']} ({s['project']}); every sign-off was "
                f'cleared. Read it with crossover_contract(crossover_id="{crossover_id}") and sign off again '
                'once your side matches it.')
            return {'crossover_id': crossover_id, 'version': version, 'body': body, 'author': s['id'], 'created': stamp}

    # ---- 9. structured evidence ----------------------------------------------

    @staticmethod
    def _evidence_items(evidence):
        if evidence is None:
            return []
        if not isinstance(evidence, list) or len(evidence) > 20:
            raise ValueError('evidence is a list of up to 20 checks')
        out = []
        for entry in evidence:
            if not isinstance(entry, dict):
                raise ValueError('Each evidence item is an object with command/exit_code/tests_passed/...')
            unknown = sorted(set(entry) - set(EVIDENCE_FIELDS))
            if unknown:
                raise ValueError(f"Unknown evidence field(s): {', '.join(unknown)}; use {', '.join(EVIDENCE_FIELDS)}")
            item = {'command': str(entry.get('command') or '')[:500],
                    'exit_code': _int(entry.get('exit_code'), 'exit_code'),
                    'tests_passed': _int(entry.get('tests_passed'), 'tests_passed'),
                    'tests_failed': _int(entry.get('tests_failed'), 'tests_failed'),
                    'commit': str(entry.get('commit') or '')[:100],
                    'output': str(entry.get('output') or '')[:2000]}
            if not item['command'] and item['tests_passed'] is None and item['tests_failed'] is None:
                raise ValueError('Each check needs a command or test counts')
            out.append(item)
        return out

    def _store_evidence(self, c, task_id, project, author, context, items):
        stamp = now()
        c.executemany('''INSERT INTO evidence(task_id,project,author,context,command,exit_code,tests_passed,
                         tests_failed,commit_ref,output,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                      [(task_id, project, author, context, i['command'], i['exit_code'], i['tests_passed'],
                        i['tests_failed'], i['commit'], i['output'], stamp) for i in items])

    def _evidence_summary(self, c, task_ids):
        if not task_ids:
            return {}
        out = {}
        for chunk in range(0, len(task_ids), 500):
            ids = task_ids[chunk:chunk + 500]
            for row in c.execute(
                    f'''SELECT task_id, COUNT(*) checks,
                        SUM(CASE WHEN (exit_code IS NOT NULL AND exit_code!=0) OR COALESCE(tests_failed,0)>0
                            THEN 1 ELSE 0 END) failing
                        FROM evidence WHERE task_id IN ({','.join('?' * len(ids))}) GROUP BY task_id''', ids):
                out[row['task_id']] = {'checks': row['checks'], 'failing': row['failing'],
                                       'level': 'failing' if row['failing'] else 'checked'}
        return out

    def _evidence_list(self, c, task_id):
        return [dict(r) for r in c.execute(
            '''SELECT context,command,exit_code,tests_passed,tests_failed,commit_ref,output,author,created
               FROM evidence WHERE task_id=? ORDER BY id DESC LIMIT 50''', (task_id,))]

    # ---- 10. stale-claim alerts ----------------------------------------------

    def _stale(self, c, project=None, hours=STALE_HOURS, limit=None):
        query = '''SELECT t.id,t.project,t.title,t.status,t.owner,t.resources,s.name,s.last_seen
                   FROM tasks t JOIN sessions s ON s.id=t.owner
                   WHERE t.status IN ('RUNNING','BLOCKED','PAUSED') AND t.human_paused=0 AND t.imported=0
                   AND s.last_seen<? AND EXISTS (SELECT 1 FROM claims WHERE task_id=t.id)'''
        args = [_ago(hours=hours)]
        if project:
            query += ' AND t.project=?'
            args.append(project)
        if limit:
            query += ' ORDER BY s.last_seen LIMIT ?'
            args.append(limit)
        else:
            query += ' ORDER BY s.last_seen'
        rows = c.execute(query, args).fetchall()
        return [{'task_id': r['id'], 'project': r['project'], 'title': r['title'], 'status': r['status'],
                 'owner': r['owner'], 'owner_name': r['name'], 'owner_last_seen': r['last_seen'],
                 'resources': json.loads(r['resources'])} for r in rows]

    def alert_stale_claims(self, hours=STALE_HOURS):
        """One message per project to the human, listing claims whose owner went quiet. Each stale episode once."""
        with self.connection(True) as c:
            fresh = [r for r in self._stale(c, hours=hours) if not c.execute(
                'SELECT 1 FROM stale_alerts WHERE task_id=? AND owner=? AND owner_last_seen=?',
                (r['task_id'], r['owner'], r['owner_last_seen'])).fetchone()]
            by_project = {}
            for row in fresh:
                by_project.setdefault(row['project'], []).append(row)
            stamp = now()
            for project, rows in by_project.items():
                lines = [f"- {r['task_id']} {r['title'][:90]} ({r['status']}); owner {r['owner_name'][:60]} "
                         f"({r['owner']}) silent since {r['owner_last_seen'][:16]}Z; holds "
                         f"{', '.join(r['resources'])[:160]}" for r in rows[:30]]
                more = f'\n…and {len(rows) - 30} more on the dashboard.' if len(rows) > 30 else ''
                self._message(c, project, SYSTEM, HUMAN,
                              f'STALE CLAIMS: {len(rows)} task(s) hold paths but their owner has not checked in for '
                              f'{hours:g}+ h. Reassign or close them on the dashboard, or let the owner resume.\n'
                              + '\n'.join(lines) + more, None, kind='stale')
                c.executemany('INSERT OR IGNORE INTO stale_alerts VALUES(?,?,?,?)',
                              [(r['task_id'], r['owner'], r['owner_last_seen'], stamp) for r in rows])
            return len(fresh)

    # ---- 11. an agent reopens a finished task it needs -------------------------

    def reopen_task(self, key, task_id, reason, next_step):
        reason = text(reason, 'reason', 2000)
        next_step = text(next_step, 'next step')
        with self.connection(True) as c:
            s = self.auth(c, key)
            t = self.task(c, task_id)
            if t['project'] != s['project']:
                raise PermissionError('Task belongs to another project')
            if t['status'] != 'DONE':
                raise Conflict('Only a completed task can be reopened; ask its owner for a handoff instead')
            # Completion released the claims; take the recorded scope back atomically,
            # so a reopened task never lands on top of someone's newer work.
            self.claim_resources(c, s['project'], task_id, t['resources'])
            self._drop_from_queues(c, s['id'], t['resources'])
            previous = t['owner']
            c.execute("UPDATE handoff_briefs SET status='superseded' WHERE task_id=? AND status='offered'", (task_id,))
            c.execute('''UPDATE tasks SET owner=?,assigned_to='',status='RUNNING',next_step=?,human_paused=0,
                         imported=0,pending_owner=NULL,version=version+1,updated=? WHERE id=?''',
                      (s['id'], next_step, now(), task_id))
            self._log(c, task_id, s['project'], s['id'], 'reopened',
                      f"Reopened by {s['name']} ({s['id']}): {reason}. Previous outcome: {t['summary'][:500]}")
            self.event(c, s['project'], s['id'], 'task.reopened',
                       {'task_id': task_id, 'session_id': s['id'], 'next_step': next_step, 'reason': reason})
            notice = f"REOPENED {task_id} \"{t['title'][:120]}\" by {s['name']} ({s['id']}): {reason}"
            if previous and previous != s['id']:
                self._message(c, s['project'], s['id'], previous, notice, task_id, kind='fyi')
            self._message(c, s['project'], s['id'], HUMAN, notice, task_id, kind='fyi')
            result = self.task(c, task_id)
            result['lessons'] = self._lessons_for_paths(c, s['project'], t['resources'])
            return result

    # ---- 6. wait_for: the state it watches ---------------------------------------

    def wait_session(self, key):
        with self.connection() as c:
            return dict(self.auth(c, key))

    def wait_state(self, session, resources=None, crossover_id=None):
        """What wait_for compares between polls. Read-only, no auth (the caller authenticated once)."""
        with self.connection() as c:
            ids = self._aliases(c, session['id'])
            marks = ','.join('?' * len(ids))
            direct = [r[0] for r in c.execute(
                f'''SELECT id FROM messages m WHERE project=? AND recipient IN ({marks}) AND sender NOT IN ({marks})
                    AND NOT EXISTS (SELECT 1 FROM receipts r WHERE r.message_id=m.id AND r.session_id IN ({marks}))''',
                (session['project'], *ids, *ids, *ids))]
            answers = [r[0] for r in c.execute(
                f"SELECT message_id FROM questions WHERE asker IN ({marks}) AND status='answered'", ids)]
            decisions = [r[0] for r in c.execute(
                f"SELECT id FROM approvals WHERE requester IN ({marks}) AND status='decided'", ids)]
            state = {'direct': sorted(direct), 'answers': sorted(answers), 'decisions': sorted(decisions)}
            if resources:
                state['holders'] = {r: sorted(h['task_id'] for h in self._holders(c, session['project'], r,
                                                                                    exclude_owner=session['id']))
                                    for r in resources}
            if crossover_id:
                view = self._crossover_view(c, crossover_id)
                state['crossover'] = {'status': view['status'], 'contract_version': view.get('contract_version'),
                                      'signed': sorted(m['project'] for m in view['members'] if m['signed_off']),
                                      'joined': sorted(m['project'] for m in view['members'] if m['task_id'])}
            return state

    @staticmethod
    def wait_changes(before, after):
        changes = []
        for key, label in (('direct', 'message'), ('answers', 'answer'), ('decisions', 'decision')):
            new = sorted(set(after[key]) - set(before[key]))
            if new:
                changes.append({'kind': label, 'ids': new})
        for resource, holders in (after.get('holders') or {}).items():
            if holders != (before.get('holders') or {}).get(resource):
                changes.append({'kind': 'resource', 'resource': resource, 'free': not holders, 'held_by': holders})
        if after.get('crossover') and after['crossover'] != before.get('crossover'):
            changes.append({'kind': 'crossover', **after['crossover']})
        return changes
