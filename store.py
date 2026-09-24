"""Transactional coordination store. No filesystem edits or deployment execution."""
import hashlib
import json
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from projects import DEFAULT_DESK, display_name, valid_slug


class Conflict(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def ident(prefix):
    return prefix + uuid4().hex[:12]


def text(value, label, limit=12000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{label} must contain 1–{limit} characters')
    return value.strip()


def scopes(values):
    if not isinstance(values, list) or not values or len(values) > 100:
        raise ValueError('Supply 1–100 relative file/directory paths or service:name resources')
    result = []
    for value in values:
        value = text(value, 'resource', 500).replace('\\', '/')
        if value.startswith('service:'):
            if not re.fullmatch(r'service:[a-zA-Z0-9_.:-]+', value):
                raise ValueError('Invalid service resource')
        else:
            if value.startswith('/') or ':' in value or any(c in value for c in '*?'):
                raise ValueError('Use literal repo-relative paths, not absolute paths or globs')
            parts = value.split('/')
            if '..' in parts:
                raise ValueError('Parent traversal is not a valid resource')
            value = '/'.join(p for p in parts if p and p != '.') or '.'
        result.append(value)
    return sorted(set(result))


def overlaps(a, b):
    if a.startswith('service:') or b.startswith('service:'):
        return a == b
    return a == '.' or b == '.' or a == b or a.startswith(b + '/') or b.startswith(a + '/')


# The human owner's identity on the board. 'rohan' is the id this project
# originally shipped with; it stays as the stored value so existing messages and
# receipts keep resolving. Clients should send 'human', which is aliased to it.
HUMAN = 'rohan'


class Store:
    CONTEXT_COMMENT_LIMIT = 50
    CONTEXT_HANDOFF_LIMIT = 20
    SNAPSHOT_HANDOFF_LIMIT = 100

    # Sections check_in can return. The default (include=None) is the whole
    # dashboard snapshot, which is what the web UI and the lifecycle hooks read
    # — but it is far too much for an agent that only wants to know what is
    # new. On a busy project it reached ~695KB and overran the caller's context
    # twice in one night, so the message the agent needed could not be read at
    # all. An agent should ask for sections instead: ['inbox','conflicts'].
    SECTIONS = ('events', 'inbox', 'board', 'my_tasks', 'counts')
    # What a caller that passes no `include` has always received.
    LEGACY_SECTIONS = ('events', 'inbox', 'board')
    # Long prose fields are kept whole in the inbox (the body IS the point) but
    # shortened in task LISTS, where forty full summaries are what blows the
    # budget. Ask for one task by id when the whole text is wanted.
    DIGEST_CHARS = 240
    DIGEST_FIELDS = ('summary', 'validation', 'next_step')

    def __init__(self, path, strict=False, default_project='default', desk_url=DEFAULT_DESK):
        self.path = Path(path)
        self.strict = bool(strict)
        self.default_project = default_project
        self.desk_url = desk_url.rstrip('/')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            c.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, secret_hash TEXT NOT NULL, name TEXT NOT NULL,
                    kind TEXT NOT NULL, project TEXT NOT NULL, branch TEXT NOT NULL,
                    worktree TEXT NOT NULL, last_seen TEXT NOT NULL, imported INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL,
                    owner TEXT, assigned_to TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                    priority TEXT NOT NULL DEFAULT 'normal', resources TEXT NOT NULL,
                    next_step TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
                    validation TEXT NOT NULL DEFAULT '', commit_ref TEXT NOT NULL DEFAULT '',
                    deployment TEXT NOT NULL DEFAULT 'not_deployed', version INTEGER NOT NULL DEFAULT 1,
                    updated TEXT NOT NULL, human_paused INTEGER NOT NULL DEFAULT 0,
                    imported INTEGER NOT NULL DEFAULT 0, pending_owner TEXT,
                    FOREIGN KEY(owner) REFERENCES sessions(id));
                CREATE TABLE IF NOT EXISTS claims (
                    project TEXT NOT NULL, resource TEXT NOT NULL, task_id TEXT NOT NULL,
                    PRIMARY KEY(project, resource, task_id), FOREIGN KEY(task_id) REFERENCES tasks(id));
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, sender TEXT NOT NULL,
                    recipient TEXT NOT NULL, body TEXT NOT NULL, task_id TEXT, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (
                    message_id TEXT NOT NULL, session_id TEXT NOT NULL, acknowledged TEXT NOT NULL,
                    PRIMARY KEY(message_id, session_id));
                CREATE TABLE IF NOT EXISTS notes (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, author TEXT NOT NULL,
                    kind TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS handoff_briefs (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, task_id TEXT NOT NULL,
                    source_session TEXT NOT NULL, target_session TEXT NOT NULL,
                    task_version INTEGER NOT NULL, progress TEXT NOT NULL,
                    remaining_work TEXT NOT NULL, validation TEXT NOT NULL,
                    risks TEXT NOT NULL, commit_ref TEXT NOT NULL, branch TEXT NOT NULL,
                    worktree TEXT NOT NULL, changed_paths TEXT NOT NULL,
                    brief TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'offered',
                    created TEXT NOT NULL, accepted TEXT, accepted_by TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(id));
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL,
                    actor TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS projects (
                    slug TEXT PRIMARY KEY, name TEXT NOT NULL, repo_roots TEXT NOT NULL DEFAULT '[]',
                    rules_path TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0,
                    created TEXT NOT NULL);
            ''')
            # Every project already in use gets a row, so the dropdown lists it.
            existing = [r[0] for r in c.execute('SELECT project FROM tasks UNION SELECT project FROM sessions')]
            c.executemany('INSERT OR IGNORE INTO projects(slug,name,created) VALUES(?,?,?)',
                          [(slug, display_name(slug), now()) for slug in existing])
        self.path.chmod(0o600)

    @contextmanager
    def connection(self, write=False):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            if write:
                c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def event(self, c, project, actor, kind, data):
        c.execute('INSERT INTO events(project,actor,kind,data,created) VALUES(?,?,?,?,?)',
                  (project, actor, kind, json.dumps(data), now()))

    def _project_row(self, row):
        item = dict(row)
        item['repo_roots'] = json.loads(item['repo_roots'])
        item['archived'] = bool(item['archived'])
        return item

    def _roots(self, values):
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, (list, tuple)) or len(values) > 20:
            raise ValueError('Give up to 20 repo folders')
        roots = []
        for value in values:
            value = text(value, 'repo folder', 1000)
            if not Path(value).is_absolute():
                raise ValueError('Repo folders must be absolute paths')
            roots.append(str(Path(value)))
        return sorted(set(roots))

    def _ensure_project(self, c, slug):
        c.execute('INSERT OR IGNORE INTO projects(slug,name,created) VALUES(?,?,?)',
                  (slug, display_name(slug), now()))

    def registry(self, c):
        return {r['slug']: json.loads(r['repo_roots'])
                for r in c.execute('SELECT slug, repo_roots FROM projects WHERE archived=0')}

    def project(self, slug):
        with self.connection() as c:
            row = c.execute('SELECT * FROM projects WHERE slug=?', (slug,)).fetchone()
            if not row:
                raise ValueError('Unknown project')
            return self._project_row(row)

    def list_projects(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        with self.connection(True) as c:
            # A session or task can name a project between constructions of this
            # Store (the one-shot __init__ backfill only sees what existed at
            # open time), so re-sync here too — otherwise a project in active
            # use would be invisible until the process restarts.
            for row in c.execute('SELECT project FROM tasks UNION SELECT project FROM sessions'):
                self._ensure_project(c, row[0])
            out = []
            for row in c.execute('SELECT * FROM projects ORDER BY archived, name').fetchall():
                slug, item = row['slug'], self._project_row(row)
                item['open_tasks'] = c.execute(
                    "SELECT COUNT(*) FROM tasks WHERE project=? AND status!='DONE'", (slug,)).fetchone()[0]
                item['unread_human'] = c.execute(
                    '''SELECT COUNT(*) FROM messages m WHERE project=? AND recipient IN (?, 'all')
                       AND sender!=? AND NOT EXISTS
                       (SELECT 1 FROM receipts r WHERE r.message_id=m.id AND r.session_id=?)''',
                    (slug, HUMAN, HUMAN, HUMAN)).fetchone()[0]
                item['live_sessions'] = c.execute(
                    'SELECT COUNT(*) FROM sessions WHERE project=? AND imported=0 AND last_seen>?',
                    (slug, cutoff)).fetchone()[0]
                item['last_activity'] = c.execute(
                    'SELECT MAX(created) FROM events WHERE project=?', (slug,)).fetchone()[0]
                item['hidden'] = item['archived'] or (slug == 'default' and item['open_tasks'] == 0)
                out.append(item)
            return out

    def create_project(self, slug, name, repo_roots=()):
        if not valid_slug(slug):
            raise ValueError('Use a lowercase project slug: letters, digits and dashes')
        name = text(name, 'project name', 120)
        roots = self._roots(list(repo_roots))
        with self.connection(True) as c:
            if c.execute('SELECT 1 FROM projects WHERE slug=?', (slug,)).fetchone():
                raise Conflict(f'Project {slug} already exists')
            c.execute('INSERT INTO projects(slug,name,repo_roots,created) VALUES(?,?,?,?)',
                      (slug, name, json.dumps(roots), now()))
            self.event(c, slug, HUMAN, 'project.created', {'name': name, 'repo_roots': roots})
            return self._project_row(c.execute('SELECT * FROM projects WHERE slug=?', (slug,)).fetchone())

    def update_project(self, slug, name=None, repo_roots=None, rules_path=None, archived=None):
        with self.connection(True) as c:
            if not c.execute('SELECT 1 FROM projects WHERE slug=?', (slug,)).fetchone():
                raise ValueError('Unknown project')
            changes = {}
            if name is not None:
                changes['name'] = text(name, 'project name', 120)
            if repo_roots is not None:
                changes['repo_roots'] = json.dumps(self._roots(repo_roots))
            if rules_path is not None:
                rules_path = rules_path.strip()
                if rules_path and not Path(rules_path).is_absolute():
                    raise ValueError('Rules file must be an absolute path')
                changes['rules_path'] = rules_path
            if archived is not None:
                changes['archived'] = 1 if archived else 0
            for column, value in changes.items():   # column names come only from the fixed keys above
                c.execute(f'UPDATE projects SET {column}=? WHERE slug=?', (value, slug))
            self.event(c, slug, HUMAN, 'project.updated',
                       {k: (json.loads(v) if k == 'repo_roots' else v) for k, v in changes.items()})
            return self._project_row(c.execute('SELECT * FROM projects WHERE slug=?', (slug,)).fetchone())

    def auth(self, c, key):
        digest = hashlib.sha256(key.encode()).hexdigest()
        row = c.execute('SELECT * FROM sessions WHERE secret_hash=?', (digest,)).fetchone()
        if not row:
            raise PermissionError('Unknown session key; register a session first')
        c.execute('UPDATE sessions SET last_seen=? WHERE id=?', (now(), row['id']))
        return dict(row)

    def register(self, name, kind, project, branch, worktree):
        if kind not in ('codex', 'claude'):
            raise ValueError('Agent kind must be codex or claude')
        text(project, 'project', 100)
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', project):
            raise ValueError('Use a lowercase project slug')
        if not Path(worktree).is_absolute():
            raise ValueError('Worktree must be an absolute path')
        key, sid = secrets.token_urlsafe(32), ident('s-')
        with self.connection(True) as c:
            c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,0)',
                      (sid, hashlib.sha256(key.encode()).hexdigest(), text(name,'name',120), kind,
                       project, text(branch,'branch',250), text(worktree,'worktree',1000), now()))
            self.event(c, project, sid, 'session.registered', {'name': name, 'kind': kind})
        return {'session_id': sid, 'session_key': key,
                'instruction': 'Keep the key private for this session; use check_in before edits and at milestones.'}

    def task(self, c, task_id):
        row = c.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise ValueError('Task not found')
        obj = dict(row)
        obj['resources'] = json.loads(obj['resources'])
        return obj

    def claim_resources(self, c, project, task_id, resources):
        for existing in c.execute("SELECT * FROM claims WHERE (project=? OR resource LIKE 'service:%') AND task_id!=?", (project, task_id)):
            if any(overlaps(resource, existing['resource']) for resource in resources):
                raise Conflict(f"Resource overlaps {existing['resource']} held by {existing['task_id']}. Request a handoff; do not edit.")
        c.execute('DELETE FROM claims WHERE task_id=?', (task_id,))
        c.executemany('INSERT INTO claims VALUES(?,?,?)', [(project,r,task_id) for r in resources])

    def own(self, c, key, task_id, version):
        session = self.auth(c, key)
        task = self.task(c, task_id)
        if session['id'] != task['owner']:
            raise PermissionError('Only the task owner can update this task')
        if version != task['version']:
            raise Conflict('Task changed; check_in and use its latest version')
        return session, task

    def claim(self, key, title, resources, next_step, task_id=None):
        resources = scopes(resources)
        with self.connection(True) as c:
            s = self.auth(c, key)
            if task_id:
                t = self.task(c, task_id)
                if t['project'] != s['project'] or t['owner'] or t['status'] != 'QUEUED':
                    raise Conflict('Task is not an unowned queued task in your project')
                if t['assigned_to'] not in ('', s['kind'], s['id']):
                    raise Conflict('Task is assigned to another agent')
                if t['human_paused']:
                    raise Conflict('The human owner paused this task')
                # Preserve the queued scope; expanding it requires an explicit update later.
                if resources != t['resources']:
                    raise Conflict('Claim the queued task using its exact resources')
                c.execute("UPDATE tasks SET owner=?,status='RUNNING',updated=?,version=version+1 WHERE id=?",
                          (s['id'], now(), task_id))
            else:
                task_id = ident('t-')
                c.execute('''INSERT INTO tasks(id,project,title,owner,status,resources,next_step,updated)
                             VALUES(?,?,?,?,'RUNNING',?,?,?)''',
                          (task_id,s['project'],text(title,'title',300),s['id'],json.dumps(resources),
                           text(next_step,'next step'),now()))
            self.claim_resources(c, s['project'], task_id, resources)
            self.event(c,s['project'],s['id'],'task.claimed', {'task_id': task_id, 'resources':resources})
            return self.task(c, task_id)

    def update(self, key, task_id, version, status, next_step, summary='', validation='', commit_ref='',
               deployment='not_deployed', resources=None):
        if status not in ('RUNNING','BLOCKED','PAUSED','DONE'):
            raise ValueError('Invalid task status')
        if deployment not in ('not_deployed','not_applicable','deployed','failed'):
            raise ValueError('Invalid deployment state')
        if status == 'DONE':
            text(summary,'completion summary'); text(validation,'validation evidence')
        else:
            text(next_step,'next step')
        with self.connection(True) as c:
            s,t = self.own(c,key,task_id,version)
            if t['human_paused'] and status != 'PAUSED':
                raise Conflict('The human owner paused this task; only they can resume or close it')
            if t['status'] == 'DONE':
                raise Conflict('Completed tasks are immutable; create a follow-up task')
            new_resources = scopes(resources) if resources is not None else t['resources']
            if status == 'DONE':
                c.execute('DELETE FROM claims WHERE task_id=?',(task_id,))
            else:
                self.claim_resources(c,s['project'],task_id,new_resources)
            c.execute("UPDATE handoff_briefs SET status='superseded' WHERE task_id=? AND status='offered'",
                      (task_id,))
            c.execute('''UPDATE tasks SET status=?,next_step=?,summary=?,validation=?,commit_ref=?,deployment=?,
                         resources=?,version=version+1,updated=?,pending_owner=NULL WHERE id=?''',
                      (status,next_step,summary,validation,commit_ref,deployment,json.dumps(new_resources),now(),task_id))
            self.event(c,s['project'],s['id'],'task.updated',
                       {'task_id':task_id,'status':status,'summary':summary,'validation':validation,'next_step':next_step})
            return self.task(c,task_id)

    def handoff(self, key, task_id, version, target_session, accept=False):
        with self.connection(True) as c:
            s = self.auth(c,key); t = self.task(c,task_id)
            if t['project'] != s['project']:
                raise PermissionError('Task belongs to another project')
            if t['version'] != version or t['status'] == 'DONE' or t['human_paused']:
                raise Conflict('Task changed, completed, or paused by the human owner')
            accepted_brief_id = None
            if accept:
                if t['pending_owner'] != s['id']:
                    raise PermissionError('No handoff addressed to this session')
                c.execute('UPDATE tasks SET owner=?,pending_owner=NULL,version=version+1,updated=? WHERE id=?',
                          (s['id'],now(),task_id))
                brief = c.execute('''SELECT id FROM handoff_briefs
                    WHERE task_id=? AND project=? AND target_session=? AND status='offered'
                    AND task_version=? ORDER BY created DESC LIMIT 1''',
                    (task_id,t['project'],s['id'],t['version'] - 1)).fetchone()
                if brief:
                    c.execute("UPDATE handoff_briefs SET status='accepted',accepted=?,accepted_by=? WHERE id=?",
                              (now(),s['id'],brief['id']))
                    accepted_brief_id = brief['id']
            else:
                if t['owner'] != s['id']:
                    raise PermissionError('Only the owner can offer a handoff')
                target=c.execute('SELECT * FROM sessions WHERE id=? AND project=? AND imported=0',
                                 (target_session,s['project'])).fetchone()
                if not target or target_session == s['id']:
                    raise ValueError('Target must be another registered session in this project')
                c.execute("UPDATE handoff_briefs SET status='superseded' WHERE task_id=? AND status='offered'",
                          (task_id,))
                c.execute('UPDATE tasks SET pending_owner=?,version=version+1,updated=? WHERE id=?',
                          (target_session,now(),task_id))
            data = {'task_id':task_id,'target':s['id'] if accept else target_session}
            if accepted_brief_id:
                data['handoff_id'] = accepted_brief_id
            self.event(c,t['project'],s['id'],'handoff.accepted' if accept else 'handoff.offered', data)
            return self.task(c,task_id)

    def prepare_handoff(self, key, task_id, version, target_session, progress,
                        remaining_work, validation, risks, commit_ref, branch,
                        worktree, changed_paths):
        """Persist a complete handoff brief and offer ownership atomically."""
        progress = text(progress, 'progress')
        remaining_work = text(remaining_work, 'remaining work')
        validation = text(validation, 'validation')
        risks = text(risks, 'risks')
        commit_ref = text(commit_ref, 'commit ref')
        branch = text(branch, 'branch', 250)
        worktree = text(worktree, 'worktree', 1000)
        if not Path(worktree).is_absolute():
            raise ValueError('Worktree must be an absolute path')
        if not isinstance(changed_paths, list) or len(changed_paths) > 100:
            raise ValueError('changed paths must be a list of 0–100 literal paths')
        changed_paths = scopes(changed_paths) if changed_paths else []
        with self.connection(True) as c:
            s = self.auth(c, key)
            t = self.task(c, task_id)
            if t['project'] != s['project']:
                raise PermissionError('Task belongs to another project')
            if t['version'] != version:
                raise Conflict('Task changed; check_in and use its latest version')
            if t['status'] == 'DONE' or t['human_paused']:
                raise Conflict('Task is completed or paused by the human owner')
            if t['owner'] != s['id']:
                raise PermissionError('Only the owner can prepare a handoff')
            target = c.execute('''SELECT id,name,kind,project,branch,worktree,last_seen,imported
                FROM sessions WHERE id=? AND project=? AND imported=0''',
                (target_session, s['project'])).fetchone()
            if not target or target_session == s['id']:
                raise ValueError('Target must be another registered session in this project')
            c.execute("UPDATE handoff_briefs SET status='superseded' WHERE task_id=? AND status='offered'",
                      (task_id,))
            brief_id = ident('h-')
            created = now()
            brief = (
                f'Progress: {progress}\nRemaining work: {remaining_work}\n'
                f'Validation: {validation}\nRisks: {risks}\nCommit: {commit_ref}\n'
                f'Branch: {branch}\nWorktree: {worktree}\n'
                f'Changed paths: {", ".join(changed_paths) if changed_paths else "(none)"}'
            )
            if len(brief) > 12000:
                raise ValueError('Handoff brief is too large; shorten its context fields')
            c.execute('''INSERT INTO handoff_briefs
                (id,project,task_id,source_session,target_session,task_version,progress,
                 remaining_work,validation,risks,commit_ref,branch,worktree,changed_paths,
                 brief,status,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (brief_id,s['project'],task_id,s['id'],target_session,version,progress,
                 remaining_work,validation,risks,commit_ref,branch,worktree,
                 json.dumps(changed_paths),brief,'offered',created))
            c.execute('''UPDATE tasks SET pending_owner=?,version=version+1,updated=? WHERE id=?''',
                      (target_session,created,task_id))
            message_body = f'Handoff offered for {t["title"]} ({task_id})\nHandoff brief: {brief_id}\n\n{brief}'
            if len(message_body) > 12000:
                raise ValueError('Handoff notification is too large; shorten its context fields')
            message = self._message(c,s['project'],s['id'],target_session,message_body,task_id)
            self.event(c,s['project'],s['id'],'handoff.prepared',{
                'task_id':task_id, 'handoff_id':brief_id, 'target':target_session,
                'progress':progress, 'remaining_work':remaining_work,
                'validation':validation, 'risks':risks, 'commit_ref':commit_ref,
                'changed_paths':changed_paths})
            return {'handoff_id':brief_id, 'message_id':message['message_id'],
                    'status':'offered', 'task':self.task(c,task_id)}

    @staticmethod
    def _safe_session(row):
        if not row:
            return None
        return {key: row[key] for key in
                ('id','name','kind','project','branch','worktree','last_seen','imported')}

    def _handoffs(self, c, project, task_id=None, limit=20):
        if task_id is None:
            rows = c.execute('''SELECT * FROM handoff_briefs WHERE project=?
                ORDER BY created DESC LIMIT ?''', (project,limit))
        else:
            rows = c.execute('''SELECT * FROM handoff_briefs
                WHERE project=? AND task_id=? ORDER BY created DESC LIMIT ?''',
                (project,task_id,limit))
        handoffs = []
        for row in rows:
            item = dict(row)
            item['changed_paths'] = json.loads(item['changed_paths'])
            source = c.execute('''SELECT id,name,kind,project,branch,worktree,last_seen,imported
                FROM sessions WHERE id=? AND project=?''',
                (item['source_session'],project)).fetchone()
            target = c.execute('''SELECT id,name,kind,project,branch,worktree,last_seen,imported
                FROM sessions WHERE id=? AND project=?''',
                (item['target_session'],project)).fetchone()
            item['source'] = self._safe_session(source)
            item['target'] = self._safe_session(target)
            handoffs.append(item)
        return handoffs

    def get_task_context(self, key, task_id):
        with self.connection(True) as c:
            s = self.auth(c, key)
            t = self.task(c, task_id)
            if t['project'] != s['project']:
                raise PermissionError('Task belongs to another project')
            comments = [dict(row) for row in c.execute('''SELECT id,project,sender,recipient,
                body,task_id,created FROM messages WHERE project=? AND task_id=?
                ORDER BY created DESC LIMIT ?''',
                (s['project'],task_id,self.CONTEXT_COMMENT_LIMIT))]
            handoffs = self._handoffs(c, s['project'], task_id, self.CONTEXT_HANDOFF_LIMIT)
            return {'project':s['project'], 'task':t, 'comments':comments,
                    'comment_limit':self.CONTEXT_COMMENT_LIMIT,
                    'handoff_briefs':handoffs,
                    'handoff_limit':self.CONTEXT_HANDOFF_LIMIT,
                    'latest_handoff':handoffs[0] if handoffs else None}

    def recipient_matches(self, s, recipient):
        return recipient in ('all', s['kind'], s['id'])

    def message(self, key, recipient, body, task_id=None):
        with self.connection(True) as c:
            s=self.auth(c,key)
            return self._message(c,s['project'],s['id'],recipient,body,task_id)

    def _message(self,c,project,sender,recipient,body,task_id=None):
        # 'human' is the name to use; 'rohan' is the original id this project
        # shipped with and is kept as an alias so existing rows and any client
        # still sending it keep working. Renaming the stored id would be a data
        # migration, not a rename.
        if recipient == 'human':
            recipient = HUMAN
        if recipient not in ('all','codex','claude',HUMAN):
            if not c.execute('SELECT 1 FROM sessions WHERE id=? AND project=?',(recipient,project)).fetchone():
                raise ValueError('Unknown recipient in this project')
        if task_id and self.task(c,task_id)['project'] != project:
            raise ValueError('Task belongs to another project')
        mid=ident('m-')
        c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)',
                  (mid,project,sender,recipient,text(body,'message'),task_id,now()))
        self.event(c,project,sender,'message.sent',{'message_id':mid,'recipient':recipient})
        return {'message_id':mid,'status':'sent','acknowledged':False}

    def _acknowledge(self,c,s,message_id):
        m=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
        if not m or m['project'] != s['project'] or not self.recipient_matches(s,m['recipient']):
            raise PermissionError('This message is not addressed to your session')
        c.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?)',(message_id,s['id'],now()))
        self.event(c,s['project'],s['id'],'message.acknowledged',{'message_id':message_id})
        return {'message_id':message_id,'acknowledged_by':s['id']}

    def acknowledge(self,key,message_id=None,message_ids=None):
        """Mark one message read, or many in one call.

        Registering into a busy project delivers the whole unread backlog at
        once — 40 messages on this project in one night — and acknowledging
        them singly is 40 round trips. A list does it in one.

        A batch reports per-message outcomes instead of aborting on the first
        id that is not yours: losing 39 good acknowledgements to one bad id is
        exactly how a backlog never clears. A single message_id keeps the old
        return shape AND the old exception, because callers depend on both.
        """
        if message_id is None and message_ids is None:
            raise ValueError('Provide message_id (one) or message_ids (several)')
        with self.connection(True) as c:
            s=self.auth(c,key)
            if message_ids is None:
                return self._acknowledge(c,s,message_id)
            if isinstance(message_ids,str):
                message_ids=[message_ids]
            ids=list(dict.fromkeys(([message_id] if message_id else [])+list(message_ids)))
            if not ids:
                raise ValueError('message_ids was empty — name at least one message')
            acknowledged,failed=[],[]
            for mid in ids:
                try:
                    self._acknowledge(c,s,mid); acknowledged.append(mid)
                except PermissionError as e:
                    failed.append({'message_id':mid,'reason':str(e)})
            return {'acknowledged':acknowledged,'failed':failed,'acknowledged_by':s['id'],
                    'counts':{'acknowledged':len(acknowledged),'failed':len(failed)}}

    def note(self,key,body,kind='note'):
        if kind not in ('note','proposal','finding'):
            raise ValueError('Agents can post notes, findings, or proposals. Only the human records decisions.')
        with self.connection(True) as c:
            s=self.auth(c,key)
            return self._note(c,s['project'],s['id'],body,kind)

    def _note(self,c,project,author,body,kind):
        nid=ident('n-')
        c.execute('INSERT INTO notes VALUES(?,?,?,?,?,?)',(nid,project,author,kind,text(body,'note'),now()))
        self.event(c,project,author,'note.created',{'note_id':nid,'kind':kind})
        return {'note_id':nid}

    def _publish_update(self, c, project, actor, title, body, commit_ref, validation, task_id=None):
        title = text(title, 'title', 300)
        body = text(body, 'body')
        commit_ref = text(commit_ref, 'commit ref')
        validation = text(validation, 'validation')
        if task_id:
            task = self.task(c, task_id)
            if task['project'] != project:
                raise ValueError('Task belongs to another project')
        formatted = (f'{title}\n\n{body}\n\nCommit: {commit_ref}\n'
                     f'Validation: {validation}')
        note = self._note(c, project, actor, formatted, 'changelog')
        message = self._message(c, project, actor, 'all', formatted, task_id)
        self.event(c, project, actor, 'changelog.published', {
            'note_id': note['note_id'], 'message_id': message['message_id'],
            'title': title, 'commit_ref': commit_ref, 'validation': validation,
            'task_id': task_id})
        return {'note_id':note['note_id'], 'message_id':message['message_id'],
                'status':'published'}

    def publish_update(self, key, title, body, commit_ref, validation, task_id=None):
        with self.connection(True) as c:
            s = self.auth(c, key)
            return self._publish_update(c, s['project'], s['id'], title, body,
                                        commit_ref, validation, task_id)

    def would_conflict(self, key, resources):
        """Who already holds these paths — WITHOUT claiming them.

        claim() already refuses an overlap, so two tasks can never hold
        overlapping resources; that is why check_in has no 'conflicts' section,
        it would be empty by construction. What is genuinely missing is the
        question you want answered BEFORE you plan around a file: is anyone on
        it? Asking by claiming creates a task you may not want and must then
        release. This is read-only and creates nothing.
        """
        if isinstance(resources, str):
            resources = [resources]
        if not resources:
            raise ValueError('Name at least one resource to check')
        with self.connection() as c:
            s = self.auth(c, key)
            blockers = []
            for existing in c.execute(
                    "SELECT * FROM claims WHERE (project=? OR resource LIKE 'service:%')", (s['project'],)):
                hits = [r for r in resources if overlaps(r, existing['resource'])]
                if not hits:
                    continue
                task = self.task(c, existing['task_id'])
                if task['owner'] == s['id']:
                    continue  # already mine; claiming again is not a conflict
                blockers.append({'resource': existing['resource'], 'your_resources': hits,
                                 'task_id': task['id'], 'title': task['title'],
                                 'owner': task['owner'], 'status': task['status'],
                                 'human_paused': task['human_paused']})
            return {'resources': resources, 'clear': not blockers, 'blockers': blockers}

    def _digest(self, row):
        """Shorten a task's long prose, leaving proof of what was cut.

        A summary or validation can legitimately run to several thousand
        characters. That is right when you open one task and ruinous in a list
        of forty. The full text stays available through get_task_context.
        """
        out = dict(row)
        for field in self.DIGEST_FIELDS:
            value = out.get(field)
            if isinstance(value, str) and len(value) > self.DIGEST_CHARS:
                out[field] = value[:self.DIGEST_CHARS].rstrip() + '…'
                out[f'{field}_chars'] = len(value)
                out['digested'] = True
        return out

    def check_in(self,key,since=0,include=None):
        """Refresh presence and report what changed.

        include=None keeps the historic response exactly: events, inbox and the
        full board snapshot. Live sessions and the hooks read response['board']
        directly, so that default must not change under them.

        Passing include returns only those sections, which is how an agent
        avoids being handed the entire dashboard on every turn.
        """
        if since < 0:
            raise ValueError('Cursor must be nonnegative')
        if include is not None:
            if isinstance(include, str):
                include = [include]
            unknown = sorted({s for s in include if s not in self.SECTIONS})
            if unknown:
                raise ValueError(f"Unknown section(s): {', '.join(unknown)}. "
                                 f"Valid sections: {', '.join(self.SECTIONS)}")
        # No include -> the historic three sections ONLY. The newer sections
        # must be asked for by name, or the default response would quietly grow
        # and every existing caller would pay for sections it never reads.
        wants = (lambda section: section in self.LEGACY_SECTIONS) if include is None \
                else (lambda section: section in include)
        with self.connection(True) as c:
            s=self.auth(c,key)
            events=[]
            if wants('events'):
                events=[dict(r) for r in c.execute('SELECT * FROM events WHERE project=? AND seq>? ORDER BY seq LIMIT 100',
                                                  (s['project'],since))]
                for e in events: e['data']=json.loads(e['data'])
            # Cursor advances only over events actually returned — so a caller
            # that did not ask for events keeps its place rather than skipping.
            out={'session_id':s['id'],'cursor':events[-1]['seq'] if events else since}
            if wants('events'):
                out['events']=events
            if wants('inbox'):
                # Bodies stay whole here: an unread message you cannot read is
                # the bug this whole parameter exists to fix.
                out['inbox']=[dict(r) for r in c.execute('''SELECT m.* FROM messages m WHERE project=?
                    AND recipient IN ('all',?,?) AND sender!=? AND NOT EXISTS
                    (SELECT 1 FROM receipts r WHERE r.message_id=m.id AND r.session_id=?) ORDER BY created LIMIT 100''',
                    (s['project'],s['kind'],s['id'],s['id'],s['id']))]
            mine=None
            if wants('my_tasks') or wants('counts'):
                mine=[self.task(c,r['id']) for r in c.execute(
                    'SELECT id FROM tasks WHERE project=? AND owner=? ORDER BY updated DESC',(s['project'],s['id']))]
            if wants('my_tasks'):
                out['my_tasks']=[self._digest(t) for t in mine]
            if wants('counts'):
                unread=c.execute('''SELECT COUNT(*) FROM messages m WHERE project=?
                    AND recipient IN ('all',?,?) AND sender!=? AND NOT EXISTS
                    (SELECT 1 FROM receipts r WHERE r.message_id=m.id AND r.session_id=?)''',
                    (s['project'],s['kind'],s['id'],s['id'],s['id'])).fetchone()[0]
                out['counts']={'unread_messages':unread,'my_tasks':len(mine),
                               'my_open_tasks':sum(1 for t in mine if t['status'] not in ('DONE','CANCELLED')),
                               'project_tasks':c.execute('SELECT COUNT(*) FROM tasks WHERE project=?',(s['project'],)).fetchone()[0],
                               'sessions':c.execute('SELECT COUNT(*) FROM sessions WHERE project=?',(s['project'],)).fetchone()[0]}
            if wants('board'):
                out['board']=self._snapshot(c,s['project'])
            return out

    def _snapshot(self,c,project):
        sessions=[dict(r) for r in c.execute('SELECT id,name,kind,project,branch,worktree,last_seen,imported FROM sessions WHERE project=?',(project,))]
        for s in sessions:
            s['stale']=(datetime.now(timezone.utc)-datetime.fromisoformat(s['last_seen'])).total_seconds()>900
        tasks=[self.task(c,r['id']) for r in c.execute('SELECT id FROM tasks WHERE project=? ORDER BY updated DESC',(project,))]
        messages=[dict(r) for r in c.execute('SELECT * FROM messages WHERE project=? ORDER BY created DESC LIMIT 200',(project,))]
        for m in messages:
            m['acknowledgments']=[dict(r) for r in c.execute('SELECT session_id,acknowledged FROM receipts WHERE message_id=?',(m['id'],))]
        return {'project':project,'sessions':sessions,'tasks':tasks,'messages':messages,
                'handoff_briefs':self._handoffs(c,project,limit=self.SNAPSHOT_HANDOFF_LIMIT),
                'notes':[dict(r) for r in c.execute('SELECT * FROM notes WHERE project=? ORDER BY created DESC LIMIT 100',(project,))],
                'events':[dict(r) for r in c.execute('SELECT * FROM events WHERE project=? ORDER BY seq DESC LIMIT 100',(project,))],
                'shared_locks':[{'resource':r['resource'],'task_id':r['task_id'],'project':r['project']}
                                for r in c.execute("SELECT * FROM claims WHERE resource LIKE 'service:%' AND project!=?",(project,))],
                'generated_at':now()}

    def snapshot(self,project):
        with self.connection() as c: return self._snapshot(c,project)

    def human(self,project,action,data):
        with self.connection(True) as c:
            if action=='create':
                resources=scopes(data['resources']); tid=ident('t-')
                if data.get('assigned_to','') not in ('','codex','claude'):
                    raise ValueError('Assign queued work to codex, claude, or either')
                c.execute('''INSERT INTO tasks(id,project,title,assigned_to,status,resources,next_step,updated)
                    VALUES(?,?,?,?,'QUEUED',?,?,?)''',
                    (tid,project,text(data['title'],'title',300),data.get('assigned_to',''),json.dumps(resources),
                     text(data['next_step'],'next step'),now()))
                self.event(c,project,'rohan','task.queued',{'task_id':tid})
                return self.task(c,tid)
            if action=='message': return self._message(c,project,'rohan',data['recipient'],data['body'],data.get('task_id'))
            if action=='note': return self._note(c,project,'rohan',data['body'],'decision')
            if action=='publish_update':
                return self._publish_update(c,project,'rohan',data['title'],data['body'],
                                            data['commit_ref'],data['validation'],data.get('task_id'))
            if action=='ack':
                m=c.execute("SELECT * FROM messages WHERE id=? AND project=? AND recipient IN ('rohan','all')",(data['message_id'],project)).fetchone()
                if not m: raise ValueError('Message not addressed to the owner')
                c.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?)',(m['id'],'rohan',now()))
                self.event(c,project,'rohan','message.acknowledged',{'message_id':m['id']})
                return {'ok':True}
            t=self.task(c,data['task_id'])
            if t['project']!=project: raise ValueError('Wrong project')
            if t['version']!=data['version']: raise Conflict('Task changed; refresh before acting')
            if action=='reopen':
                if t['status']!='DONE': raise Conflict('Only completed tasks can be reopened')
                target=c.execute('SELECT * FROM sessions WHERE id=? AND project=? AND imported=0',(data['session_id'],project)).fetchone()
                if not target: raise ValueError('Choose a registered session in this project')
                next_step=text(data.get('next_step',''),'next step')
                # Completion releases claims. Reopening reacquires the exact
                # recorded scope atomically, so a finished task cannot quietly
                # resume on top of another agent's newer work.
                self.claim_resources(c,project,t['id'],t['resources'])
                c.execute("UPDATE handoff_briefs SET status='superseded' WHERE task_id=? AND status='offered'",(t['id'],))
                c.execute("""UPDATE tasks SET owner=?,assigned_to='',status='RUNNING',next_step=?,
                           human_paused=0,imported=0,pending_owner=NULL WHERE id=?""",
                          (target['id'],next_step,t['id']))
                c.execute('UPDATE tasks SET version=version+1,updated=? WHERE id=?',(now(),t['id']))
                self.event(c,project,'rohan','task.reopened',{
                    'task_id':t['id'],'session_id':target['id'],'next_step':next_step})
                return self.task(c,t['id'])
            if t['status']=='DONE': raise Conflict('Completed task; use Reopen & reassign')
            if action=='pause':
                c.execute("UPDATE tasks SET status='PAUSED',human_paused=1,pending_owner=NULL WHERE id=?",(t['id'],))
                c.execute("UPDATE handoff_briefs SET status='paused' WHERE task_id=? AND status='offered'",(t['id'],))
            elif action=='resume':
                c.execute('UPDATE tasks SET status=?,human_paused=0 WHERE id=?',('RUNNING' if t['owner'] else 'QUEUED',t['id']))
            elif action=='priority':
                if data['priority'] not in ('normal','high','urgent'): raise ValueError('Invalid priority')
                c.execute('UPDATE tasks SET priority=? WHERE id=?',(data['priority'],t['id']))
                c.execute("UPDATE tasks SET pending_owner=NULL WHERE id=?",(t['id'],))
                c.execute("UPDATE handoff_briefs SET status='superseded' WHERE task_id=? AND status='offered'",(t['id'],))
            elif action=='reassign':
                target=c.execute('SELECT * FROM sessions WHERE id=? AND project=? AND imported=0',(data['session_id'],project)).fetchone()
                if not target: raise ValueError('Choose a registered session in this project')
                text(data.get('reason',''),'handoff reason')
                self.claim_resources(c,project,t['id'],t['resources'])
                c.execute("UPDATE handoff_briefs SET status='reassigned' WHERE task_id=? AND status='offered'",(t['id'],))
                c.execute("UPDATE tasks SET owner=?,imported=0,pending_owner=NULL,status=CASE WHEN status='QUEUED' THEN 'RUNNING' ELSE status END WHERE id=?",(target['id'],t['id']))
            elif action=='close':
                text(data.get('summary',''),'closure reason')
                c.execute("UPDATE tasks SET status='DONE',summary=?,validation='Closed by the owner; not a test result',pending_owner=NULL WHERE id=?",(data['summary'],t['id']))
                c.execute('DELETE FROM claims WHERE task_id=?',(t['id'],))
                c.execute("UPDATE handoff_briefs SET status='closed' WHERE task_id=? AND status='offered'",(t['id'],))
            else: raise ValueError('Unknown action')
            c.execute('UPDATE tasks SET version=version+1,updated=? WHERE id=?',(now(),t['id']))
            self.event(c,project,'rohan','task.'+action,data)
            return self.task(c,t['id'])

    def backup(self,directory):
        directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
        destination=directory / ('desk-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
        with self.connection() as src:
            dst=sqlite3.connect(destination)
            try: src.backup(dst)
            finally: dst.close()
        destination.chmod(0o600)
        for old in sorted(directory.glob('desk-*.sqlite3'))[:-48]: old.unlink()
        return destination
