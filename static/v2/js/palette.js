import {html, useState, useEffect, useRef, useContext} from '../vendor/preact-htm.module.js';
import {Desk} from './context.js';
import * as api from './api.js';
import {whoFor, taskOf, firstLine} from './util.js';

export const VIEWS = [['command', 'Command center'], ['tasks', 'Tasks'], ['actions', 'Action points'], ['inbox', 'Inbox'],
  ['notes', 'Decisions & notes'], ['claims', 'Claims & locks'], ['lanes', 'Agent lanes'], ['lessons', 'Lessons'], ['activity', 'Activity'], ['projects', 'Projects']];

export function Palette({onClose}) {
  const ctx = useContext(Desk);
  const [text, setText] = useState('');
  const [i, setI] = useState(0);
  const [found, setFound] = useState(null);
  const input = useRef(null);
  useEffect(() => { input.current && input.current.focus(); }, []);
  useEffect(() => {
    const t = text.trim();
    if (t.length < 2) { setFound(null); return undefined; }
    const timer = setTimeout(() => api.search(ctx.project(), t).then(setFound).catch(() => setFound(null)), 220);
    return () => clearTimeout(timer);
  }, [text]);
  const b = ctx.board, A = ctx.actions, sel = taskOf(b, ctx.sel);
  const run = fn => () => { onClose(); fn(); };
  const cmds = [
    ...VIEWS.map(([v, l]) => ({group: 'Go to', label: l, run: run(() => ctx.go(v))})),
    ...(sel ? (sel.status === 'DONE' ? [{group: 'Task', label: `Reopen “${sel.title}”`, sub: 're-claims recorded paths', run: run(() => A.reopen(sel))}]
      : [{group: 'Task', label: `${sel.human_paused ? 'Resume' : 'Pause'} “${sel.title}”`, sub: 'claims stay reserved', run: run(() => (sel.human_paused ? A.resume(sel) : A.pause(sel)))},
         {group: 'Task', label: `Reassign “${sel.title}”`, sub: 'to a live session', run: run(() => A.reassign(sel))},
         {group: 'Task', label: `Close “${sel.title}” with note`, sub: `releases ${sel.resources.length} claim(s)`, run: run(() => A.close(sel))}]) : []),
    {group: 'Create', label: 'Add task', sub: 'refused if its paths overlap an open claim', run: run(() => A.newTask())},
    {group: 'Create', label: 'Write message', sub: 'to a session, an agent kind, or everyone', run: run(() => A.message())},
    {group: 'Create', label: 'Record a decision', sub: "reaches every agent's hook", run: run(() => A.decision())},
    {group: 'Create', label: 'Add lesson', sub: 'shared memory for every agent', run: run(() => A.lesson())},
    {group: 'Create', label: 'Add action points', sub: 'your own to-do lines', run: run(() => A.addActionItems())},
    {group: 'Check', label: 'Would it conflict?', sub: 'look up who holds a path', run: run(() => ctx.go('claims'))},
    {group: 'Desk', label: 'Connect agents', sub: 'paste line for this repo', run: run(() => A.connect())},
    {group: 'Desk', label: 'Announce a desk restart', sub: 'every board; message VS Code peers too', run: run(() => A.announce())},
    ...(ctx.projects || []).filter(p => p.slug !== ctx.project() && !p.archived).map(p => ({group: 'Project', label: `Switch to ${p.name}`, sub: p.slug, run: run(() => ctx.setProject(p.slug))})),
    ...(b.tasks || []).map(t => ({group: 'Open', label: t.title, sub: `${t.id} · ${t.status} · ${whoFor(b, t.owner).name}`, run: run(() => ctx.select(t.id, {open: true, view: ctx.view === 'command' ? 'command' : 'tasks'}))})),
    ...(b.sessions || []).filter(s => !s.stale).map(s => ({group: 'Agent', label: s.name, sub: `${s.branch || ''} · seen ${s.last_seen ? s.last_seen.slice(11, 16) : ''}`, run: run(() => {
      const t = (b.tasks || []).find(x => x.owner === s.id && x.status !== 'DONE'); if (t) ctx.select(t.id, {open: true, view: 'tasks'}); else ctx.go('tasks');
    })})),
  ];
  const q = text.trim().toLowerCase();
  let list = cmds.filter(c => !q || `${c.group} ${c.label} ${c.sub || ''}`.toLowerCase().includes(q)).slice(0, 12);
  if (found) {
    const extra = [
      ...(found.messages || []).map(m => ({group: 'Message', label: m.snippet || firstLine(m.body, 120), sub: `${whoFor(b, m.sender).name} · ${m.created ? m.created.slice(0, 16).replace('T', ' ') : ''}`, run: run(() => { ctx.ui.inboxTab = 'all'; if (m.task_id) ctx.select(m.task_id, {open: true, view: 'inbox'}); else ctx.go('inbox'); })})),
      ...(found.notes || []).map(n => ({group: n.kind === 'decision' ? 'Decision' : 'Note', label: n.snippet || firstLine(n.body, 120), sub: `${whoFor(b, n.author).name}`, run: run(() => ctx.go('notes'))})),
      ...(found.lessons || []).map(l => ({group: 'Lesson', label: l.snippet || firstLine(l.body, 120), sub: (l.paths || []).join(', '), run: run(() => ctx.go('lessons'))})),
      ...(found.tasks || []).filter(t => !list.some(c => c.sub && c.sub.startsWith(t.id))).map(t => ({group: 'Task', label: t.title, sub: `${t.id} · ${t.status}`, run: run(() => ctx.openTask(t.id))})),
    ];
    list = [...list, ...extra].slice(0, 24);
  }
  const at = Math.min(i, Math.max(0, list.length - 1));
  const onKey = e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setI(Math.min(list.length - 1, at + 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setI(Math.max(0, at - 1)); }
    else if (e.key === 'Enter') { e.preventDefault(); if (list[at]) list[at].run(); }
    else if (e.key === 'Escape') { e.preventDefault(); onClose(); }
  };
  return html`<div class="scrim" onClick=${onClose}>
    <div class="palette" role="dialog" aria-label="Command palette" onClick=${e => e.stopPropagation()}>
      <input ref=${input} placeholder="Type a command, task, path, agent — or search messages and decisions" value=${text}
        onInput=${e => { setText(e.target.value); setI(0); }} onKeyDown=${onKey} aria-label="Search" />
      <div class="pal-list">${list.map((c, n) => html`<button type="button" class=${`pal-item${n === at ? ' on' : ''}`} onMouseEnter=${() => n !== at && setI(n)} onClick=${c.run}>
          <span class="g">${c.group}</span><span class="l"><b>${c.label}</b>${c.sub ? html`<span>${c.sub}</span>` : ''}</span><span class="k">${n === at ? '⏎' : ''}</span></button>`)}
        ${list.length ? '' : html`<div class="empty">No match.</div>`}</div>
      <div class="pal-foot"><span>↑↓ move · ⏎ run · esc close</span><span>Recorded as you (rohan)</span></div>
    </div></div>`;
}
