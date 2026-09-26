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

from projects import DECLARATION, DEFAULT_DESK, display_name, resolve_project, valid_slug
from deskcore import Conflict, HUMAN, ident, now, overlaps, scopes, text  # noqa: F401  (re-exported)
from desk_features import FeaturesMixin, MESSAGE_KINDS, infer_kind


class Store(FeaturesMixin):
    CONTEXT_COMMENT_LIMIT = 50
    CONTEXT_HANDOFF_LIMIT = 20
    SNAPSHOT_HANDOFF_LIMIT = 100

    # Sections check_in can return. The default (include=None) is the whole
    # dashboard snapshot, which is what the web UI and the lifecycle hooks read
    # — but it is far too much for an agent that only wants to know what is
    # new. On a busy project it reached ~695KB and overran the caller's context
    # twice in one night, so the message the agent needed could not be read at
    # all. An agent should ask for sections instead: ['inbox','conflicts'].
    SECTIONS = ('events', 'inbox', 'board', 'my_tasks', 'counts', 'crossovers',
                'inbox_digest', 'questions', 'approvals', 'queues')
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
                -- Crossover. Side tables only: the messages table keeps its original
                -- seven columns, because earlier code inserts into it positionally and a
                -- rollback to that code must still run against this database.
                CREATE TABLE IF NOT EXISTS message_links (
                    message_id TEXT PRIMARY KEY, origin_project TEXT NOT NULL, crossover_id TEXT);
                CREATE TABLE IF NOT EXISTS crossovers (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, origin_project TEXT NOT NULL,
                    origin_task TEXT NOT NULL, created_by TEXT NOT NULL,
                    created TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS crossover_members (
                    crossover_id TEXT NOT NULL, project TEXT NOT NULL, task_id TEXT,
                    invited_by TEXT NOT NULL, invited TEXT NOT NULL, joined TEXT, joined_by TEXT,
                    signoff TEXT NOT NULL DEFAULT '', signed_by TEXT, signed TEXT,
                    PRIMARY KEY(crossover_id, project), FOREIGN KEY(crossover_id) REFERENCES crossovers(id));
                CREATE INDEX IF NOT EXISTS crossover_members_task ON crossover_members(task_id);
            ''')
            self._migrate_features(c)
            self._sync_registry(c)
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

    def _sync_registry(self, c):
        """Give every project slug already referenced by tasks or sessions a
        row, so the listing (and the dropdown it feeds) never misses one.

        The single copy of this backfill logic: __init__ calls it once at
        open time, and list_projects calls it (only when needed — see there)
        to catch a slug that started being used after the Store was opened.
        Takes a write connection; callers decide when that is warranted.
        """
        existing = [r[0] for r in c.execute('SELECT project FROM tasks UNION SELECT project FROM sessions')]
        c.executemany('INSERT OR IGNORE INTO projects(slug,name,created) VALUES(?,?,?)',
                      [(slug, display_name(slug), now()) for slug in existing])

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
        # A session or task can name a project between constructions of this
        # Store (the one-shot __init__ backfill only sees what existed at open
        # time), so this must still catch up on a slug used since then —
        # but a listing is polled constantly by every live session, and
        # BEGIN IMMEDIATE on every call would hold SQLite's one writer slot
        # and stall register/claim/message/ack for the whole fleet even when
        # nothing needs inserting. So: check on a plain read connection first,
        # and only pay for a write transaction on the rare call that finds a
        # gap to fill.
        with self.connection() as c:
            missing = c.execute('''SELECT project FROM tasks UNION SELECT project FROM sessions
                                    EXCEPT SELECT slug FROM projects''').fetchall()
        if missing:
            with self.connection(True) as c:
                self._sync_registry(c)
        with self.connection() as c:
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

    def move_project(self, source, target, worktree_prefix, apply=False, idle_hours=6, export_path=''):
        """Move a repo's records between projects. Dry run unless apply=True.

        Only sessions under worktree_prefix (a path, or a list of paths — a
        prefix matches a worktree that equals it or sits under it as a whole
        path segment, never an unrelated sibling like Shop-App-Reports)
        and idle for idle_hours move, with the tasks they own and those tasks'
        claims, messages, notes and handoff briefs. Live sessions stay put:
        their hook binding records the old project. Events stay as history.
        All-or-nothing.
        """
        if not (valid_slug(source) and valid_slug(target)) or source == target:
            raise ValueError('Name two different project slugs')
        values = [worktree_prefix] if isinstance(worktree_prefix, str) else list(worktree_prefix)
        if not values:
            raise ValueError('Name at least one worktree prefix')
        prefixes = []
        for value in values:
            if isinstance(value, str):
                value = value.rstrip('/')
            value = text(value, 'worktree prefix', 1000)
            if not Path(value).is_absolute():
                raise ValueError('Worktree prefix must be an absolute path')
            prefixes.append(value)
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=idle_hours)).isoformat()
        with self.connection(True) as c:
            under = [dict(r) for r in c.execute(
                'SELECT id,name,worktree,last_seen FROM sessions WHERE project=?', (source,))
                if any(r['worktree'] == p or r['worktree'].startswith(p + '/') for p in prefixes)]
            moving = [s for s in under if s['last_seen'] <= cutoff]
            live = [s for s in under if s['last_seen'] > cutoff]
            ids = [s['id'] for s in moving]
            marks = ','.join('?' * len(ids))
            tasks = [dict(r) for r in c.execute(
                f'SELECT id,title,status,resources FROM tasks WHERE project=? AND owner IN ({marks})',
                (source, *ids))] if ids else []
            task_ids = [t['id'] for t in tasks]
            tmarks = ','.join('?' * len(task_ids))
            linked = [r[0] for r in c.execute(
                f'SELECT task_id FROM crossover_members WHERE task_id IN ({tmarks})', task_ids)] if task_ids else []
            if linked and apply:   # a crossover records each side's project; moving would orphan it
                raise Conflict(f"Tasks {', '.join(linked)} are in a crossover; finish or close them before moving")
            for t in tasks:                      # open claims must not collide in the target
                if t['status'] == 'DONE':
                    continue
                for existing in c.execute('SELECT * FROM claims WHERE project=? AND task_id!=?', (target, t['id'])):
                    if any(overlaps(r, existing['resource']) for r in json.loads(t['resources'])):
                        raise Conflict(f"Task {t['id']} would overlap {existing['resource']} "
                                       f"held by {existing['task_id']} in {target}")
            message_ids = [r['id'] for r in c.execute(
                f'''SELECT id FROM messages WHERE project=? AND (sender IN ({marks}) OR recipient IN ({marks})
                    {f"OR task_id IN ({tmarks})" if task_ids else ''})''',
                (source, *ids, *ids, *task_ids))] if ids else []
            note_ids = [r['id'] for r in c.execute(
                f'SELECT id FROM notes WHERE project=? AND author IN ({marks})', (source, *ids))] if ids else []
            brief_ids = [r['id'] for r in c.execute(
                f'SELECT id FROM handoff_briefs WHERE project=? AND task_id IN ({tmarks})',
                (source, *task_ids))] if task_ids else []
            report = {'source': source, 'target': target, 'apply': bool(apply),
                      'sessions': moving, 'skipped_live': live,
                      'tasks': [{'id': t['id'], 'title': t['title'], 'status': t['status']} for t in tasks],
                      'messages': len(message_ids), 'notes': len(note_ids), 'handoff_briefs': len(brief_ids)}
            if not apply or not ids:
                return report
            self._ensure_project(c, target)
            for table, key, ids_for_table in (('sessions', 'id', ids), ('tasks', 'id', task_ids),
                                              ('claims', 'task_id', task_ids), ('messages', 'id', message_ids),
                                              ('notes', 'id', note_ids), ('handoff_briefs', 'id', brief_ids)):
                if ids_for_table:
                    c.execute(f"UPDATE {table} SET project=? WHERE {key} IN ({','.join('?' * len(ids_for_table))})",
                              (target, *ids_for_table))
            joined_prefixes = ', '.join(prefixes)
            summary = (f"Moved {len(ids)} sessions and {len(task_ids)} tasks here from '{source}' "
                       f"(worktrees under {joined_prefixes}), with {len(message_ids)} messages and {len(note_ids)} notes."
                       + (f' Export of the originals: {export_path}' if export_path else ''))
            self._note(c, target, 'project-desk', summary, 'note')
            for project in (source, target):
                self.event(c, project, 'project-desk', 'project.moved',
                           {'from': source, 'to': target, 'sessions': ids, 'tasks': task_ids,
                            'worktree_prefixes': joined_prefixes})
            return report

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
        requested = (project or '').strip()
        if requested and not valid_slug(requested):
            raise ValueError('Use a lowercase project slug')
        if not isinstance(worktree, str) or not Path(worktree).is_absolute():
            raise ValueError('Worktree must be an absolute path')
        # Resolve outside the write transaction: it may run git (bounded by a timeout).
        with self.connection() as c:
            registry = self.registry(c)
        declared = resolve_project(worktree, registry)
        hint = None
        if declared:
            if requested and requested != declared.project:
                raise ValueError(f'This worktree belongs to project {declared.project} '
                                 f'(declared by {DECLARATION} or the desk for {declared.root}). '
                                 f'Register with project {declared.project}, or omit project.')
            project = declared.project
        elif self.strict:
            raise ValueError('This worktree is not connected to a Project Desk project, so the desk '
                             'cannot tell which project you belong to. Ask the human to connect this '
                             f'repo at {self.desk_url}/projects, then register again.')
        else:
            project = requested or self.default_project
            hint = ('This worktree declares no project. Connect the repo (it adds '
                    f'{DECLARATION}) at {self.desk_url}/projects so every agent here lands '
                    'in the same project.')
        key, sid = secrets.token_urlsafe(32), ident('s-')
        with self.connection(True) as c:
            self._ensure_project(c, project)
            c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,0)',
                      (sid, hashlib.sha256(key.encode()).hexdigest(), text(name,'name',120), kind,
                       project, text(branch,'branch',250), text(worktree,'worktree',1000), now()))
            self.event(c, project, sid, 'session.registered', {'name': name, 'kind': kind})
            resumable = self._resumable(c, {'id': sid, 'project': project, 'kind': kind, 'worktree': worktree})
        result = {'session_id': sid, 'session_key': key, 'project': project,
                  'instruction': 'Keep the key private for this session; use check_in before edits and at milestones.'}
        if resumable:
            result['resumable'] = resumable
            result['resume_hint'] = ('Earlier sessions of this agent in this worktree still own open tasks. If one '
                                     'was you before a restart, call resume_session(session_key, from_session).')
        if hint:
            result['hint'] = hint
        return result

    def session_mismatches(self, hours=6):
        """Recently seen sessions whose worktree now declares a different project."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self.connection() as c:
            registry = self.registry(c)
            rows = [dict(r) for r in c.execute(
                'SELECT id,name,kind,project,worktree,last_seen FROM sessions WHERE imported=0 AND last_seen>?',
                (cutoff,))]
        report = []
        for row in rows:
            declared = resolve_project(row['worktree'], registry)
            if declared and declared.project != row['project']:
                report.append({**row, 'declared': declared.project, 'source': declared.source})
        return report

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
                task_id = self._insert_task(c, s, title, resources, next_step)
            self.claim_resources(c, s['project'], task_id, resources)
            self.event(c,s['project'],s['id'],'task.claimed', {'task_id': task_id, 'resources':resources})
            self._log(c, task_id, s['project'], s['id'], 'claimed', f"Claimed by {s['name']}: {', '.join(resources)}")
            self._drop_from_queues(c, s['id'], resources)
            self._notify_queues(c)
            result = self.task(c, task_id)
            result['lessons'] = self._lessons_for_paths(c, s['project'], resources)
            return result

    def _insert_task(self, c, s, title, resources, next_step):
        task_id = ident('t-')
        c.execute('''INSERT INTO tasks(id,project,title,owner,status,resources,next_step,updated)
                     VALUES(?,?,?,?,'RUNNING',?,?,?)''',
                  (task_id,s['project'],text(title,'title',300),s['id'],json.dumps(resources),
                   text(next_step,'next step'),now()))
        return task_id

    def update(self, key, task_id, version, status, next_step, summary='', validation='', commit_ref='',
               deployment='not_deployed', resources=None, evidence=None):
        evidence = self._evidence_items(evidence)
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
            crossover_id = self._crossover_of_task(c, task_id) if status == 'DONE' else None
            if crossover_id:
                self._require_signoffs(c, crossover_id, task_id)
                # Finishing with evidence is this side's sign-off.
                c.execute('''UPDATE crossover_members SET signoff=?,signed_by=?,signed=?
                             WHERE crossover_id=? AND task_id=? AND signed IS NULL''',
                          (validation,s['id'],now(),crossover_id,task_id))
                c.execute('UPDATE crossovers SET updated=? WHERE id=?',(now(),crossover_id))
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
            self._log(c, task_id, s['project'], s['id'], status.lower(),
                      f'{status}: ' + (summary if status == 'DONE' else next_step))
            if evidence:
                self._store_evidence(c, task_id, s['project'], s['id'], 'task', evidence)
            if deployment == 'deployed' and commit_ref and (t['deployment'] != 'deployed' or t['commit_ref'] != commit_ref):
                for service in [r for r in t['resources'] if r.startswith('service:')]:
                    self._record_deploy(c, s['project'], s['id'], service, commit_ref, summary or next_step, task_id)
            self._notify_queues(c)
            if crossover_id:
                self._crossover_notice(c, crossover_id, s,
                    f"{s['project']} finished its side of crossover {crossover_id} ({task_id}): {summary}\n"
                    f"Validation: {validation}")
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
            if t['project'] != s['project'] and not self._shares_crossover(c, s['project'], task_id):
                raise PermissionError('Task belongs to another project')
            comments = [dict(row) for row in c.execute('''SELECT id,project,sender,recipient,
                body,task_id,created FROM messages WHERE project=? AND task_id=?
                ORDER BY created DESC LIMIT ?''',
                (t['project'],task_id,self.CONTEXT_COMMENT_LIMIT))]
            handoffs = self._handoffs(c, t['project'], task_id, self.CONTEXT_HANDOFF_LIMIT)
            crossover_id = self._crossover_of_task(c, task_id)
            return {'project':t['project'], 'task':t, 'comments':self._decorate(c, comments),
                    'crossovers':[self._crossover_view(c, crossover_id)] if crossover_id else [],
                    'journal':self._journal(c, task_id),
                    'evidence':self._evidence_list(c, task_id),
                    'lessons':self._lessons_for_paths(c, t['project'], t['resources']),
                    'comment_limit':self.CONTEXT_COMMENT_LIMIT,
                    'handoff_briefs':handoffs,
                    'handoff_limit':self.CONTEXT_HANDOFF_LIMIT,
                    'latest_handoff':handoffs[0] if handoffs else None}

    def recipient_matches(self, s, recipient):
        return recipient in ('all', s['kind'], s['id']) or recipient in s.get('aliases', ())

    def message(self, key, recipient, body, task_id=None, kind=None, reply_to=None):
        with self.connection(True) as c:
            s=self.auth(c,key)
            if isinstance(recipient,str) and recipient.startswith('x-'):
                if task_id:
                    raise ValueError('A crossover message goes to every member; it takes no task_id')
                return self._crossover_message(c,s['project'],s['id'],recipient,body)
            return self._message(c,s['project'],s['id'],recipient,body,task_id,kind=kind,reply_to=reply_to)

    def _open_project(self, c, slug):
        row = c.execute('SELECT archived FROM projects WHERE slug=?', (slug,)).fetchone()
        if not row:
            raise ValueError(f'Unknown project {slug}')
        if row['archived']:
            raise ValueError(f'Project {slug} is archived')

    def _route(self, c, project, recipient):
        """Where a message lands: (project, stored recipient).

        Same-project forms are unchanged. Two forms cross into another project:
        a session id registered there, or '<project>:all|claude|codex|human'.
        The message is stored in the RECEIVING project, so its inbox, receipts,
        hooks and dashboard work exactly as for a local message.
        """
        if not isinstance(recipient, str) or not recipient:
            raise ValueError('Unknown recipient in this project')
        if recipient in ('all','codex','claude',HUMAN):
            return project, recipient
        if ':' in recipient:
            slug, _, kind = recipient.partition(':')
            kind = HUMAN if kind == 'human' else kind
            if not valid_slug(slug) or kind not in ('all','codex','claude',HUMAN):
                raise ValueError("Address another project as '<project>:all', ':claude', ':codex' or ':human'")
            if slug != project:
                self._open_project(c, slug)
            return slug, kind
        row = c.execute('SELECT project, imported FROM sessions WHERE id=?', (recipient,)).fetchone()
        if row and row['project'] == project:
            return project, recipient
        if row and not row['imported']:
            self._open_project(c, row['project'])
            return row['project'], recipient
        raise ValueError('Unknown recipient in this project (or any other)')

    def _message(self,c,project,sender,recipient,body,task_id=None,crossover_id=None,kind=None,reply_to=None):
        # 'human' is the name to use; 'rohan' is the original id this project
        # shipped with and is kept as an alias so existing rows and any client
        # still sending it keep working. Renaming the stored id would be a data
        # migration, not a rename.
        if recipient == 'human':
            recipient = HUMAN
        target, recipient = self._route(c, project, recipient)
        cross = target != project
        if task_id:
            task = self.task(c,task_id)
            # A message's task always belongs to the project the message is
            # stored in; across projects, only a task the two share a crossover on.
            if task['project'] != target or (cross and not self._shares_crossover(c, project, task_id)):
                raise ValueError('Task belongs to another project')
        if kind is not None and kind not in MESSAGE_KINDS:
            raise ValueError(f"Unknown message kind {kind}; kinds: {', '.join(MESSAGE_KINDS)}")
        body=text(body,'message')
        mid=ident('m-')
        c.execute('INSERT INTO messages(id,project,sender,recipient,body,task_id,created) VALUES(?,?,?,?,?,?,?)',
                  (mid,target,sender,recipient,body,task_id,now()))
        if cross or crossover_id:
            c.execute('INSERT INTO message_links VALUES(?,?,?)', (mid, project, crossover_id))
        if reply_to:
            self._answer(c, project, sender, reply_to, mid)
            kind = kind or ('answer' if c.execute("SELECT 1 FROM questions WHERE message_id=? AND answer_message_id=?",
                                                  (reply_to, mid)).fetchone() else None)
        c.execute('INSERT INTO message_meta VALUES(?,?,?)', (mid, kind or infer_kind(body), reply_to))
        data = {'message_id':mid,'recipient':recipient}
        if cross:
            data['from_project'] = project
        self.event(c,target,sender,'message.sent',data)
        if cross:
            # Not 'message.sent': the sender's hooks would look for it in their own inbox.
            self.event(c,project,sender,'message.sent_cross',
                       {'message_id':mid,'to_project':target,'recipient':recipient})
        result = {'message_id':mid,'status':'sent','acknowledged':False}
        if cross:
            result['to_project'] = target
        return result

    def _decorate(self, c, messages):
        """Mark messages that came from another project or a crossover thread.

        Adds from_project/from_name and crossover_id only where they apply, so a
        local message looks exactly as it always did.
        """
        ids = [m['id'] for m in messages]
        if not ids:
            return messages
        marks = ','.join('?' * len(ids))
        links = {r['message_id']: r for r in c.execute(
            f"SELECT * FROM message_links WHERE message_id IN ({marks})", ids)}
        meta = {r['message_id']: r for r in c.execute(f"SELECT * FROM message_meta WHERE message_id IN ({marks})", ids)}
        asked = {r['message_id']: r['status'] for r in c.execute(
            f"SELECT message_id,status FROM questions WHERE message_id IN ({marks})", ids)}
        for m in messages:
            if m['id'] in meta:
                m['kind'] = meta[m['id']]['kind']
                if meta[m['id']]['reply_to']:
                    m['reply_to'] = meta[m['id']]['reply_to']
            if m['id'] in asked:
                m['question_status'] = asked[m['id']]
            link = links.get(m['id'])
            if not link:
                continue
            if link['origin_project'] != m['project']:
                m['from_project'] = link['origin_project']
                sender = c.execute('SELECT name FROM sessions WHERE id=?', (m['sender'],)).fetchone()
                m['from_name'] = 'Human' if m['sender'] == HUMAN else (sender['name'] if sender else m['sender'])
            if link['crossover_id']:
                m['crossover_id'] = link['crossover_id']
        return messages

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
            s['aliases']=self._aliases(c,s['id'])
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
            clean = scopes(resources)
            queues = {r: self._queue_view(c, r, s['project']) for r in clean
                      if c.execute('SELECT 1 FROM resource_queue WHERE resource=?', (r,)).fetchone()}
            return {'resources': resources, 'clear': not blockers, 'blockers': blockers,
                    'lessons': self._lessons_for_paths(c, s['project'], clean), **({'queues': queues} if queues else {})}

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
            # This session plus any earlier session it resumed: their mail is its mail.
            ids=self._aliases(c,s['id'])
            if wants('inbox'):
                # Bodies stay whole here: an unread message you cannot read is
                # the bug this whole parameter exists to fix.
                out['inbox']=self._decorate(c,self._unread_rows(c,s,ids,100))
            if wants('inbox_digest'):
                out['inbox_digest']=self._inbox_digest(c,s,ids,self._unread_rows(c,s,ids,200))
            mine=None
            if wants('my_tasks') or wants('counts'):
                mine=[self.task(c,r['id']) for r in c.execute(
                    'SELECT id FROM tasks WHERE project=? AND owner=? ORDER BY updated DESC',(s['project'],s['id']))]
            if wants('my_tasks'):
                out['my_tasks']=[self._digest(t) for t in mine]
            if wants('counts'):
                pending=self._unread_rows(c,s,ids,100000)
                unread=len(pending)
                direct=sum(1 for m in pending if m['recipient'] in ids)
                marks=','.join('?'*len(ids))
                out['counts']={'unread_messages':unread,'unread_direct':direct,'unread_broadcast':unread-direct,
                               'open_questions_for_me':len(self._questions(c,s,ids)['for_me']),
                               'my_pending_approvals':c.execute(f"SELECT COUNT(*) FROM approvals WHERE status='pending' AND requester IN ({marks})",ids).fetchone()[0],
                               'my_tasks':len(mine),
                               'my_open_tasks':sum(1 for t in mine if t['status'] not in ('DONE','CANCELLED')),
                               'project_tasks':c.execute('SELECT COUNT(*) FROM tasks WHERE project=?',(s['project'],)).fetchone()[0],
                               'sessions':c.execute('SELECT COUNT(*) FROM sessions WHERE project=?',(s['project'],)).fetchone()[0]}
            if wants('crossovers'):
                out['crossovers']=self._crossovers_for(c,s['project'])
            if wants('questions'):
                out['questions']=self._questions(c,s,ids)
            if wants('approvals'):
                marks=','.join('?'*len(ids))
                out['approvals']=[self._approval_row(c,r[0]) for r in c.execute(
                    f'SELECT id FROM approvals WHERE requester IN ({marks}) ORDER BY created DESC LIMIT 20',ids)]
            if wants('queues'):
                marks=','.join('?'*len(ids))
                out['queues']=[{'resource':r['resource'],'queue':self._queue_view(c,r['resource'],s['project']),
                                'held_by':self._holders(c,s['project'],r['resource'],exclude_owner=s['id'])}
                               for r in c.execute(f'SELECT DISTINCT resource FROM resource_queue WHERE session_id IN ({marks})',ids)]
            if wants('board'):
                out['board']=self._snapshot(c,s['project'])
            return out

    def _snapshot(self,c,project):
        sessions=[dict(r) for r in c.execute('SELECT id,name,kind,project,branch,worktree,last_seen,imported FROM sessions WHERE project=?',(project,))]
        for s in sessions:
            s['stale']=(datetime.now(timezone.utc)-datetime.fromisoformat(s['last_seen'])).total_seconds()>900
        tasks=[self.task(c,r['id']) for r in c.execute('SELECT id FROM tasks WHERE project=? ORDER BY updated DESC',(project,))]
        messages=self._decorate(c,[dict(r) for r in c.execute('SELECT * FROM messages WHERE project=? ORDER BY created DESC LIMIT 200',(project,))])
        # Sent from here into another project: stored there, listed here too.
        outgoing=self._decorate(c,[dict(r) for r in c.execute('''SELECT m.* FROM messages m JOIN message_links l
            ON l.message_id=m.id WHERE l.origin_project=? AND m.project!=? ORDER BY m.created DESC LIMIT 50''',
            (project,project))])
        for m in outgoing:
            m['to_project']=m['project']
        for m in messages+outgoing:
            m['acknowledgments']=[dict(r) for r in c.execute('SELECT session_id,acknowledged FROM receipts WHERE message_id=?',(m['id'],))]
        return {'project':project,'sessions':sessions,'tasks':tasks,'messages':messages,
                'handoff_briefs':self._handoffs(c,project,limit=self.SNAPSHOT_HANDOFF_LIMIT),
                'notes':[dict(r) for r in c.execute('SELECT * FROM notes WHERE project=? ORDER BY created DESC LIMIT 100',(project,))],
                'events':[dict(r) for r in c.execute('SELECT * FROM events WHERE project=? ORDER BY seq DESC LIMIT 100',(project,))],
                'shared_locks':[{'resource':r['resource'],'task_id':r['task_id'],'project':r['project']}
                                for r in c.execute("SELECT * FROM claims WHERE resource LIKE 'service:%' AND project!=?",(project,))],
                'crossovers':self._crossovers_for(c,project,include_closed=True),
                'outgoing_messages':outgoing,
                'lessons':[{**l,'body':l['body'][:400]} for l in
                           (self._lesson_row(c,r,compact=True) for r in c.execute(
                               'SELECT * FROM lessons WHERE archived=0 AND project IN (?,?) ORDER BY updated DESC LIMIT 40',
                               (project,'*')))],
                'approvals':[self._approval_row(c,r[0]) for r in c.execute(
                    "SELECT id FROM approvals WHERE project=? AND (status='pending' OR decided>?) ORDER BY created DESC LIMIT 30",
                    (project,(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()))],
                'open_questions':[{'message_id':r['message_id'],'asker':r['asker'],'recipient':r['recipient'],'created':r['created']}
                                  for r in c.execute("SELECT * FROM questions WHERE project=? AND status='open' ORDER BY created DESC LIMIT 50",(project,))],
                'queues':[{'resource':r['resource'],'queue':self._queue_view(c,r['resource'],project)} for r in c.execute(
                    "SELECT DISTINCT resource FROM resource_queue WHERE project=? OR resource LIKE 'service:%'",(project,))],
                'prod':self._prod(c)['services'],
                'stale_claims':self._stale(c,project),
                'task_logs':{tid:self._journal(c,tid,3) for tid in [t['id'] for t in tasks if t['status']!='DONE'][:100]},
                'evidence':self._evidence_summary(c,[t['id'] for t in tasks]),
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
            if action=='approval.decide':
                return self._decide_approval(c,project,data)
            if action=='lesson.create':
                paths=data.get('paths') or []
                if isinstance(paths,str): paths=[p.strip() for p in paths.splitlines() if p.strip()]
                tags=data.get('tags') or []
                if isinstance(tags,str): tags=[t.strip() for t in re.split(r'[,\s]+',tags) if t.strip()]
                return self._remember(c,project,HUMAN,data.get('body',''),paths,tags,data.get('scope') or 'project')
            if action=='lesson.archive':
                return self._forget(c,project,HUMAN,data.get('lesson_id',''),data.get('reason','') or 'Archived by the human')
            if action=='crossover.start':
                return self._start_crossover(c,project,HUMAN,'Human',self.task(c,data['task_id']),
                                             data.get('invite',[]),data.get('note',''))
            if action=='message':
                if str(data['recipient']).startswith('x-'):
                    return self._crossover_message(c,project,'rohan',data['recipient'],data['body'])
                return self._message(c,project,'rohan',data['recipient'],data['body'],data.get('task_id'),
                                     reply_to=data.get('reply_to'))
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
                self._log(c,t['id'],project,HUMAN,'reopened',f"Reopened by the human for {target['name']}: {next_step}")
                self._notify_queues(c)
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
            detail={'close':data.get('summary',''),'reassign':data.get('reason',''),'priority':data.get('priority','')}.get(action,'')
            self._log(c,t['id'],project,HUMAN,action,f'Human: {action}'+(f' — {detail}' if detail else ''))
            self._notify_queues(c)
            return self.task(c,t['id'])

    # ---- Crossover: two projects working one piece of work ----------------
    #
    # A crossover links one task per project. Each side keeps an ordinary task
    # in its OWN project, with its own claims, owner and handoffs, so nothing
    # about claims or project isolation changes for anyone not in one. What the
    # link adds: members read each other's task, one address (the crossover id)
    # reaches every member, and no side can finish until every other joined
    # side has signed off.

    def _crossover_of_task(self, c, task_id):
        row = c.execute('SELECT crossover_id FROM crossover_members WHERE task_id=?', (task_id,)).fetchone()
        return row['crossover_id'] if row else None

    def _shares_crossover(self, c, project, task_id):
        """True when `project` is invited to or joined the crossover `task_id` belongs to."""
        return c.execute('''SELECT 1 FROM crossover_members mine JOIN crossover_members theirs
                            ON mine.crossover_id=theirs.crossover_id
                            WHERE theirs.task_id=? AND mine.project=?''', (task_id, project)).fetchone() is not None

    def _require_signoffs(self, c, crossover_id, task_id):
        waiting = c.execute('''SELECT m.project, m.task_id FROM crossover_members m JOIN tasks t ON t.id=m.task_id
                               WHERE m.crossover_id=? AND m.task_id!=? AND m.signed IS NULL
                               AND t.status!='DONE' ORDER BY m.project''', (crossover_id, task_id)).fetchall()
        if waiting:
            who = ', '.join(f"{w['project']} ({w['task_id']})" for w in waiting)
            raise Conflict(f'Crossover {crossover_id} still needs sign-off from {who}. They call '
                           'sign_off_crossover (or finish their task); only the human can close around them.')

    def _crossover_row(self, c, crossover_id):
        row = c.execute('SELECT * FROM crossovers WHERE id=?', (crossover_id,)).fetchone()
        if not row:
            raise ValueError(f'Unknown crossover {crossover_id}')
        return row

    def _crossover_view(self, c, crossover_id):
        x = self._crossover_row(c, crossover_id)
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        members = []
        for m in c.execute('''SELECT * FROM crossover_members WHERE crossover_id=?
                              ORDER BY project!=?, invited, project''', (crossover_id, x['origin_project'])):
            item = {'project': m['project'], 'role': 'origin' if m['project'] == x['origin_project'] else 'member',
                    'task_id': m['task_id'], 'invited': m['invited'], 'invited_by': m['invited_by'],
                    'joined': m['joined'], 'signed_off': m['signed'] is not None, 'signoff': m['signoff'],
                    'signed_by': m['signed_by'], 'signed': m['signed'], 'signed_version': m['signed_version']}
            if m['task_id']:
                t = c.execute('SELECT title,status,owner FROM tasks WHERE id=?', (m['task_id'],)).fetchone()
                owner = c.execute('SELECT name,kind,last_seen FROM sessions WHERE id=?',
                                  (t['owner'],)).fetchone() if t and t['owner'] else None
                item.update({'task_title': t['title'] if t else '', 'task_status': t['status'] if t else 'MISSING',
                             'owner': t['owner'] if t else None,
                             'owner_name': owner['name'] if owner else '',
                             'owner_kind': owner['kind'] if owner else '',
                             'owner_stale': (owner['last_seen'] <= cutoff) if owner else True})
            members.append(item)
        joined = [m for m in members if m['task_id']]
        if joined and all(m['task_status'] == 'DONE' for m in joined):
            status = 'closed'
        elif len(joined) > 1 and all(m['signed_off'] for m in joined):
            status = 'validated'
        else:
            status = 'open'
        return {'id': x['id'], 'title': x['title'], 'origin_project': x['origin_project'],
                'origin_task': x['origin_task'], 'created_by': x['created_by'],
                'created': x['created'], 'updated': x['updated'], 'status': status, 'members': members,
                'awaiting_signoff': [m['project'] for m in joined
                                     if not m['signed_off'] and m['task_status'] != 'DONE'],
                'awaiting_join': [m['project'] for m in members if not m['task_id']],
                'contract_version': self._contract_version(c, x['id']),
                'talk': f'send_message(recipient="{x["id"]}") reaches every other member',
                'join_url': f'{self.desk_url}/x/{x["id"]}',
                'paste_line': f'Join Project Desk crossover {x["id"]} ("{x["title"]}") from {self.desk_url}/x/{x["id"]}'}

    def crossover(self, crossover_id):
        """Read-only view for the join page; the desk is loopback-only like /api/state."""
        with self.connection() as c:
            return self._crossover_view(c, crossover_id)

    def _crossovers_for(self, c, project, include_closed=False, limit=20):
        ids = [r[0] for r in c.execute('''SELECT x.id FROM crossovers x JOIN crossover_members m
                                          ON m.crossover_id=x.id WHERE m.project=? ORDER BY x.updated DESC''',
                                       (project,))]
        views = [self._crossover_view(c, xid) for xid in ids]
        if not include_closed:
            views = [v for v in views if v['status'] != 'closed']
        return views[:limit]

    def _crossover_recipients(self, c, crossover_id, sender):
        return [r['owner'] for r in c.execute('''SELECT t.owner FROM crossover_members m
                    JOIN tasks t ON t.id=m.task_id WHERE m.crossover_id=? AND t.owner IS NOT NULL
                    AND t.owner!=? GROUP BY t.owner ORDER BY MIN(m.invited)''', (crossover_id, sender))]

    def _crossover_message(self, c, project, sender, crossover_id, body):
        """One message per other member: the owner of each joined task hears it."""
        self._crossover_row(c, crossover_id)
        if not c.execute('SELECT 1 FROM crossover_members WHERE crossover_id=? AND project=?',
                         (crossover_id, project)).fetchone():
            raise PermissionError(f'Project {project} is not part of crossover {crossover_id}')
        recipients = self._crossover_recipients(c, crossover_id, sender)
        if not recipients:
            raise ValueError(f'Nobody else has joined crossover {crossover_id} yet; '
                             'message the invited session or project directly')
        ids = [self._message(c, project, sender, owner, body, None, crossover_id)['message_id']
               for owner in recipients]
        return {'message_id': ids[0], 'message_ids': ids, 'recipients': recipients,
                'status': 'sent', 'acknowledged': False}

    def _crossover_notice(self, c, crossover_id, session, body):
        """A best-effort note to the other members; silent when nobody else joined."""
        if self._crossover_recipients(c, crossover_id, session['id']):
            self._crossover_message(c, session['project'], session['id'], crossover_id, body)

    def start_crossover(self, key, task_id, invite, note=''):
        """Open a crossover from a task you own, inviting other projects or their sessions.

        Calling it again on a task that is already in a crossover invites more
        projects into that same crossover (and re-sends a pending invite).
        """
        with self.connection(True) as c:
            s = self.auth(c, key)
            t = self.task(c, task_id)
            if t['project'] != s['project'] or t['owner'] != s['id']:
                raise PermissionError('Only the owner of a task in your project can open a crossover from it')
            return self._start_crossover(c, s['project'], s['id'], s['name'], t, invite, note)

    def _start_crossover(self, c, project, actor, actor_name, t, invite, note):
        """Shared by an agent (owner of the task) and the human's dashboard."""
        if isinstance(invite, str):
            invite = [invite]
        if not isinstance(invite, list) or not invite or len(invite) > 20:
            raise ValueError('Invite 1–20 other projects (slug) or sessions (s-… id)')
        note = (note or '').strip()
        if len(note) > 4000:
            raise ValueError('Keep the invite note under 4000 characters')
        task_id = t['id']
        if t['project'] != project:
            raise ValueError('Task belongs to another project')
        if t['status'] == 'DONE':
            raise Conflict('Completed task; start the crossover from an open task')
        targets = []
        for item in invite:
            item = text(item, 'invite', 120)
            if item.startswith('s-'):
                row = c.execute('SELECT project FROM sessions WHERE id=? AND imported=0', (item,)).fetchone()
                if not row:
                    raise ValueError(f'Unknown session {item}')
                target = (row['project'], item)
            elif valid_slug(item):
                target = (item, None)
            else:
                raise ValueError(f'Invite a project slug or a session id, not {item!r}')
            if target[0] == project:
                raise ValueError(f'{item} is in your own project; a crossover invites another project. '
                                 'Inside your project use send_message or a handoff.')
            self._open_project(c, target[0])
            targets.append(target)
        crossover_id = self._crossover_of_task(c, task_id)
        stamp = now()
        if not crossover_id:
            crossover_id = ident('x-')
            c.execute('INSERT INTO crossovers VALUES(?,?,?,?,?,?,?)',
                      (crossover_id, t['title'], project, task_id, actor, stamp, stamp))
            c.execute('''INSERT INTO crossover_members(crossover_id,project,task_id,invited_by,invited,joined,joined_by)
                         VALUES(?,?,?,?,?,?,?)''', (crossover_id, project, task_id, actor, stamp, stamp, actor))
            self.event(c, project, actor, 'crossover.started', {'crossover_id': crossover_id, 'task_id': task_id})
        sent = []
        for target_project, session_id in targets:
            member = c.execute('SELECT task_id FROM crossover_members WHERE crossover_id=? AND project=?',
                               (crossover_id, target_project)).fetchone()
            if member and member['task_id']:
                continue   # already joined
            if not member:
                c.execute('''INSERT INTO crossover_members(crossover_id,project,invited_by,invited)
                             VALUES(?,?,?,?)''', (crossover_id, target_project, actor, stamp))
            body = (f'CROSSOVER INVITE {crossover_id} from project {project}: {actor_name} ({actor}) '
                    f'asks your project to work with them on "{t["title"]}" (their task {task_id}).\n'
                    + (f'{note}\n' if note else '') +
                    f'Join: join_crossover(crossover_id="{crossover_id}", next_step="...", '
                    'resources=["paths in YOUR repo"]) creates your own claimed task, or pass '
                    'task_id="<an open task you own>" to link work already claimed.\n'
                    f'Talk: send_message(recipient="{crossover_id}") reaches every member; '
                    f'send_message(recipient="{actor}") reaches the inviter directly.\n'
                    f'Join page with the full steps: {self.desk_url}/x/{crossover_id}\n'
                    'Finish: every joined side signs off (sign_off_crossover, or DONE with validation) '
                    'before any side can mark its task DONE.\n'
                    'Tools missing? Reconnect the project-desk MCP server (/mcp) or use the desk CLI.')
            sent.append(self._message(c, project, actor, session_id or f'{target_project}:all', body,
                                      None, crossover_id)['message_id'])
            self.event(c, target_project, actor, 'crossover.invited',
                       {'crossover_id': crossover_id, 'from_project': project})
        c.execute('UPDATE crossovers SET updated=? WHERE id=?', (stamp, crossover_id))
        view = self._crossover_view(c, crossover_id)
        view['invite_message_ids'] = sent
        return view

    def join_crossover(self, key, crossover_id, next_step, resources=None, task_id=None, title=''):
        """Join a crossover your project was invited to, with a new or an existing task of yours."""
        with self.connection(True) as c:
            s = self.auth(c, key)
            x = self._crossover_row(c, crossover_id)
            member = c.execute('SELECT * FROM crossover_members WHERE crossover_id=? AND project=?',
                               (crossover_id, s['project'])).fetchone()
            if not member:
                raise PermissionError(f'Your project {s["project"]} is not invited to {crossover_id}; '
                                      'a member can invite it with start_crossover')
            if member['task_id']:
                raise Conflict(f'Your project already joined {crossover_id} with task {member["task_id"]}; '
                               "message the crossover, or ask that task's owner for a handoff")
            if task_id:
                t = self.task(c, task_id)
                if t['project'] != s['project'] or t['owner'] != s['id']:
                    raise PermissionError('Link only an open task you own in your own project')
                if t['status'] == 'DONE':
                    raise Conflict('Completed task; join with a new task instead')
                other = self._crossover_of_task(c, task_id)
                if other:
                    raise Conflict(f'Task {task_id} is already in crossover {other}')
            else:
                if resources is None:
                    raise ValueError('Give resources (paths in your own repo) for a new task, '
                                     'or task_id of an open task you own')
                resources = scopes(resources)
                label = (title or '').strip() or f'Crossover {crossover_id}: {x["title"]}'
                task_id = self._insert_task(c, s, label[:300], resources, next_step)
                self.claim_resources(c, s['project'], task_id, resources)
                self.event(c, s['project'], s['id'], 'task.claimed', {'task_id': task_id, 'resources': resources})
            stamp = now()
            c.execute('UPDATE crossover_members SET task_id=?,joined=?,joined_by=? WHERE crossover_id=? AND project=?',
                      (task_id, stamp, s['id'], crossover_id, s['project']))
            c.execute('UPDATE crossovers SET updated=? WHERE id=?', (stamp, crossover_id))
            self.event(c, s['project'], s['id'], 'crossover.joined', {'crossover_id': crossover_id, 'task_id': task_id})
            joined_task = self.task(c, task_id)
            self._crossover_notice(c, crossover_id, s,
                f'{s["name"]} ({s["id"]}, project {s["project"]}) joined crossover {crossover_id} with task '
                f'{task_id}: {joined_task["title"]}. Paths: {", ".join(joined_task["resources"])}. '
                f'Next: {joined_task["next_step"]}')
            return self._crossover_view(c, crossover_id)

    def sign_off_crossover(self, key, crossover_id, validation, evidence=None):
        """Record your side's validation of the joint work. The owner of your side's task signs."""
        validation = text(validation, 'validation evidence')
        evidence = self._evidence_items(evidence)
        with self.connection(True) as c:
            s = self.auth(c, key)
            self._crossover_row(c, crossover_id)
            member = c.execute('SELECT * FROM crossover_members WHERE crossover_id=? AND project=?',
                               (crossover_id, s['project'])).fetchone()
            if not member or not member['task_id']:
                raise PermissionError(f'Your project has not joined {crossover_id}')
            t = self.task(c, member['task_id'])
            if t['owner'] != s['id']:
                raise PermissionError(f'Only the owner of {t["id"]} signs off for {s["project"]}')
            stamp = now()
            c.execute('''UPDATE crossover_members SET signoff=?,signed_by=?,signed=?,signed_version=?
                         WHERE crossover_id=? AND project=?''',
                      (validation, s['id'], stamp, self._contract_version(c, crossover_id), crossover_id, s['project']))
            if evidence:
                self._store_evidence(c, t['id'], s['project'], s['id'], f'signoff:{crossover_id}', evidence)
            c.execute('UPDATE crossovers SET updated=? WHERE id=?', (stamp, crossover_id))
            self.event(c, s['project'], s['id'], 'crossover.signed_off', {'crossover_id': crossover_id, 'task_id': t['id']})
            view = self._crossover_view(c, crossover_id)
            notice = f'SIGN-OFF {crossover_id} from {s["project"]} ({s["name"]}, task {t["id"]}): {validation}'
            if view['status'] == 'validated':
                notice += '\nAll sides validated. Each side may now mark its task DONE.'
            elif view['awaiting_signoff']:
                notice += f'\nStill waiting on: {", ".join(view["awaiting_signoff"])}.'
            self._crossover_notice(c, crossover_id, s, notice)
            return view

    def list_peers(self, key, project='', hours=24):
        """Who you can talk to: the projects on this desk, or one project's recent sessions."""
        if not isinstance(hours, int) or not 1 <= hours <= 720:
            raise ValueError('hours must be 1–720')
        with self.connection() as c:
            s = self.auth(c, key)
            cutoff15 = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
            if not project:
                return {'your_project': s['project'], 'projects': [
                    {'slug': r['slug'], 'name': r['name'], 'yours': r['slug'] == s['project'],
                     'live_sessions': c.execute('''SELECT COUNT(*) FROM sessions WHERE project=?
                                                   AND imported=0 AND last_seen>?''',
                                                (r['slug'], cutoff15)).fetchone()[0]}
                    for r in c.execute('SELECT slug,name FROM projects WHERE archived=0 ORDER BY name').fetchall()]}
            self._open_project(c, project)
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            sessions = [dict(r) for r in c.execute(
                '''SELECT id,name,kind,branch,last_seen FROM sessions WHERE project=? AND imported=0
                   AND last_seen>? ORDER BY last_seen DESC LIMIT 50''', (project, cutoff))]
            for item in sessions:
                item['stale'] = item['last_seen'] <= cutoff15
            return {'project': project, 'sessions': sessions,
                    'talk': f'send_message(recipient="<session id>") or recipient="{project}:all|claude|codex"'}

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
