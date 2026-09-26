import {html, useState, useEffect, useContext} from '../../vendor/preact-htm.module.js';
import {Desk} from '../context.js';
import * as api from '../api.js';
import {Seg, Btn} from '../ui.js';
import {Who, whoFor, taskOf, hm, when, firstLine, HUMAN} from '../util.js';

const forYou = m => ['rohan', 'all', 'human'].includes(m.recipient);
const readByYou = m => (m.acknowledgments || []).some(a => a.session_id === HUMAN);

// ---- Inbox -------------------------------------------------------------------
function Message({m, outgoing}) {
  const ctx = useContext(Desk);
  const [full, setFull] = useState(false);
  const b = ctx.board, w = whoFor(b, m.sender, {name: m.from_name});
  const unread = !outgoing && forYou(m) && !readByYou(m) && m.sender !== HUMAN;
  const t = m.task_id ? taskOf(b, m.task_id) : null;
  const acks = (m.acknowledgments || []).map(a => a.session_id).filter(id => id !== m.sender);
  const long = String(m.body).length > 600 || String(m.body).split('\n').length > 7;
  const kind = m.kind && m.kind !== 'message' ? `${m.kind}${m.question_status ? ' · ' + m.question_status : ''}` : '';
  const to = outgoing ? `${m.to_project}:${m.recipient}` : m.recipient === 'all' ? 'everyone' : whoFor(b, m.recipient).name;
  return html`<div class=${`msg${unread ? ' unread' : ''}`}>
    <span class="rail" style=${{background: w.kind === 'claude' ? 'var(--claude)' : w.kind === 'codex' ? 'var(--codex)' : w.kind === 'human' ? 'var(--human)' : 'var(--none)'}}></span>
    <div class="hd"><div class="l"><${Who} w=${w} />${m.from_project ? html`<span class="proj-tag">${m.from_project}</span>` : ''}<span class="to">to</span><span class="tn">${to}</span>
      ${kind ? html`<span class="kind-tag">${kind}</span>` : ''}
      ${t ? html`<button type="button" class="task-link" title=${t.title} onClick=${() => ctx.select(t.id, {open: true, view: 'tasks'})}>Task: ${t.title}</button>` : ''}</div>
      <span class="at">${when(m.created)}</span></div>
    <div class=${`bd${long && !full ? ' clamp' : ''}`}>${m.body}</div>
    <div class="ft"><span class=${`r ${acks.length ? 'rc-ok' : 'rc-wait'}`}>${acks.length ? `Read by ${acks.slice(0, 6).map(id => whoFor(b, id).short).join(', ')}${acks.length > 6 ? ` +${acks.length - 6}` : ''}` : 'Sent · awaiting read'}</span>
      <div class="acts">${long ? html`<${Btn} size="xs" label=${full ? 'Less' : 'More'} onClick=${() => setFull(!full)} />` : ''}
        ${outgoing ? '' : html`<${Btn} size="xs" label="Reply" onClick=${() => ctx.actions.message(m.sender, m.task_id || '', m.id, m.sender, m.from_project ? `${m.from_name || m.sender} (project ${m.from_project})` : '')} />`}
        ${unread ? html`<${Btn} size="xs" label="Acknowledge" onClick=${() => ctx.actions.ack(ctx.project(), m.id)} />` : ''}</div></div>
  </div>`;
}

export function Inbox() {
  const ctx = useContext(Desk);
  const b = ctx.board;
  const tab = ctx.ui.inboxTab || 'you';
  const msgs = b.messages || [];
  const unread = msgs.filter(m => forYou(m) && !readByYou(m) && m.sender !== HUMAN);
  const list = tab === 'sent' ? (b.outgoing_messages || []) : msgs.filter(m => tab === 'all' ? true : tab === 'unread' ? unread.includes(m) : forYou(m) || m.sender === HUMAN);
  return html`<section class="sec narrow">
    <div class="sec-head"><div class="sec-title"><h2 class="h2">Inbox</h2><span class="n">${unread.length} unread for you</span></div>
      <div class="sec-actions">
        <${Seg} tabs=${[['you', 'For you'], ['unread', 'Unread', unread.length], ['all', 'Everything'], ['sent', 'Sent to other projects']]} value=${tab} onPick=${k => { ctx.ui.inboxTab = k; ctx.rerender(); }} />
        <button type="button" class="btn lg" onClick=${() => ctx.actions.ackAll()}>Acknowledge all broadcasts</button>
        <button type="button" class="btn lg p" onClick=${() => ctx.actions.message()}>Write message</button></div></div>
    <div class="card flat">${list.slice(0, 120).map(m => html`<${Message} key=${m.id} m=${m} outgoing=${tab === 'sent'} />`)}
      ${list.length ? '' : html`<div class="empty">${tab === 'unread' ? 'Inbox clear.' : 'Nothing here.'}</div>`}</div>
  </section>`;
}

// ---- Action points ---------------------------------------------------------------
export function ActionPoints() {
  const ctx = useContext(Desk);
  const b = ctx.board, A = ctx.actions;
  const filter = ctx.ui.aiFilter || 'human';
  const all = b.action_items || [];
  const shown = all.filter(i => filter === 'done' ? i.status !== 'open' : i.status === 'open' && (filter === 'all' || (filter === 'human' ? i.assignee === HUMAN : i.assignee !== HUMAN)));
  const groups = new Map();
  for (const i of shown) {
    const k = i.task_id || '';
    if (!groups.has(k)) groups.set(k, {key: k, title: i.task_id ? (i.task_title || i.task_id) : 'Not linked to a task', status: i.task_id ? (i.task_status || '') : '', items: []});
    groups.get(k).items.push(i);
  }
  const rank = g => !g.key ? 2 : g.status === 'DONE' ? 1 : 0;
  const list = [...groups.values()].sort((x, y) => rank(x) - rank(y) || String(y.items[0].created).localeCompare(String(x.items[0].created)));
  const openState = ctx.ui.aiOpen || (ctx.ui.aiOpen = {});
  const resolved = filter === 'done';
  const LABEL = {done: 'done', dropped: 'cleared', superseded: 'replaced'};
  const clearMenu = () => {
    const dayAgo = d => Date.now() - d * 864e5;
    const choices = [['finished', 'On finished (DONE) tasks', shown.filter(i => i.task_status === 'DONE')],
      ['old', 'Older than 3 days', shown.filter(i => Date.parse(i.created) < dayAgo(3))],
      ['unlinked', 'Not linked to a task', shown.filter(i => !i.task_id)], ['shown', 'Everything in this list', shown]];
    const first = choices.find(([, , x]) => x.length);
    ctx.dialog({title: 'Clear action points', save: 'Clear', hint: 'Cleared points move to “Recently done” for 3 days; untick one there to bring it back.',
      fields: [{key: 'which', label: 'Which', type: 'select', value: first ? first[0] : 'shown', options: choices.map(([v, l, x]) => ({value: v, label: `${l} (${x.length})`, disabled: !x.length}))}],
      onSubmit: f => { const pick = choices.find(([v]) => v === f.which); return A.clearItems(ctx.project(), (pick ? pick[2] : []).map(i => i.id)); }});
  };
  return html`<section class="sec narrow">
    <div class="sec-head"><div class="sec-title"><h2 class="h2">Action points</h2><span class="sub">What agents left, grouped by task. A task's newest list replaces its older one.</span></div>
      <div class="sec-actions">
        <${Seg} tabs=${[['human', 'For you', all.filter(i => i.status === 'open' && i.assignee === HUMAN).length], ['agents', 'For agents'], ['all', 'Everything open'], ['done', 'Recently done']]} value=${filter} onPick=${k => { ctx.ui.aiFilter = k; ctx.rerender(); }} />
        ${!resolved && shown.length ? html`<button type="button" class="btn lg" onClick=${clearMenu}>Clear…</button>` : ''}
        <button type="button" class="btn lg p" onClick=${() => A.addActionItems()}>Add</button></div></div>
    <div class="card flat">
      ${list.map(g => {
        const isOpen = openState[g.key] ?? g.status !== 'DONE';
        return html`<div class="ai-group">
          <div class="ai-group-h" onClick=${() => { openState[g.key] = !isOpen; ctx.rerender(); }}>
            <span class="car">${isOpen ? '▾' : '▸'}</span>${g.status ? html`<span class=${`status st-${g.status}`}>${g.status}</span>` : ''}
            <span class="t" title=${g.title}>${g.key ? html`<button type="button" class="link-btn" onClick=${e => { e.stopPropagation(); ctx.select(g.key, {open: true, view: 'tasks'}); }}>${g.title}</button>` : g.title}</span>
            <span class="c">${g.items.length} item${g.items.length === 1 ? '' : 's'}</span>
            ${resolved ? '' : html`<${Btn} size="xs" label="Clear" onClick=${() => A.clearItems(ctx.project(), g.items.map(i => i.id), g.title.slice(0, 60))} />`}
          </div>
          ${isOpen ? html`<div class="items">${g.items.map(i => html`<label class=${`ai${i.status !== 'open' ? ' done' : ''}`}>
            <input type="checkbox" checked=${i.status !== 'open'} onChange=${e => A.tick(ctx.project(), [i.id], e.target.checked ? 'done' : 'open')} />
            <span class="b">${i.body}<span class="m">for ${i.assignee === HUMAN ? 'you' : i.assignee_name || i.assignee} · ${i.author_name || i.author} · ${when(i.created)}${i.status !== 'open' ? ` · ${LABEL[i.status] || i.status}${i.resolved_by_name ? ' by ' + i.resolved_by_name : ''}` : ''}${i.resolution ? ' — ' + i.resolution : ''}</span></span></label>`)}</div>` : ''}
        </div>`;
      })}
      ${list.length ? '' : html`<div class="empty">Nothing here. Agents add what they leave for you when they finish a task.</div>`}
    </div>
  </section>`;
}

// ---- Decisions & notes ---------------------------------------------------------------
export function Notes() {
  const ctx = useContext(Desk);
  const b = ctx.board;
  const tab = ctx.ui.noteTab || 'all';
  const notes = (b.notes || []).filter(n => tab === 'all' || n.kind === tab);
  return html`<section class="sec narrow">
    <div class="sec-head"><div class="sec-title"><h2 class="h2">Decisions & notes</h2><span class="sub">Durable context. Your decisions reach every agent's hook.</span></div>
      <div class="sec-actions">
        <${Seg} tabs=${[['all', 'All'], ['decision', 'Decisions'], ['changelog', 'Updates'], ['note', 'Notes']]} value=${tab} onPick=${k => { ctx.ui.noteTab = k; ctx.rerender(); }} />
        <button type="button" class="btn lg" onClick=${() => ctx.actions.publishUpdate()}>Publish update</button>
        <button type="button" class="btn lg p" onClick=${() => ctx.actions.decision()}>Add decision</button></div></div>
    <div class="card flat">${notes.map(n => html`<div class="note-row">
        <div class="hd"><span class=${`nk nk-${n.kind}`}>${n.kind}</span><${Who} w=${whoFor(b, n.author)} /><span>${when(n.created)}</span><span class="mono muted2">${n.id}</span></div>
        <div class="bd">${n.body}</div></div>`)}
      ${notes.length ? '' : html`<div class="empty">No notes yet.</div>`}</div>
  </section>`;
}

// ---- Lessons ---------------------------------------------------------------------
export function Lessons() {
  const ctx = useContext(Desk);
  const lessons = ctx.board.lessons || [];
  return html`<section class="sec narrow">
    <div class="sec-head"><div class="sec-title"><h2 class="h2">Lessons</h2><span class="sub">Shared memory. Agents get these when they claim the paths.</span></div>
      <button type="button" class="btn lg p" onClick=${() => ctx.actions.lesson()}>Add lesson</button></div>
    <div class="card flat">${lessons.map(l => html`<div class="lesson"><div><div class="b">${l.body}</div>
        <div class="m"><span>${l.author_name} · ${l.scope}</span>${(l.paths || []).map(p => html`<span class="path-tag">${p}</span>`)}${(l.tags || []).length ? html`<span>#${l.tags.join(' #')}</span>` : ''}</div></div>
        <${Btn} label="Archive" onClick=${() => ctx.actions.archiveLesson(l)} /></div>`)}
      ${lessons.length ? '' : html`<div class="empty">No lessons yet. Agents add them with remember; add one here too.</div>`}</div>
  </section>`;
}

// ---- Activity --------------------------------------------------------------------
export function Activity() {
  const ctx = useContext(Desk);
  const [rows, setRows] = useState(null);
  const [more, setMore] = useState(true);
  const load = async before => {
    const r = await api.events(ctx.project(), before, 60);
    if (!r) { setRows((ctx.board.events || []).map(e => ({...e, text: describe(e)}))); setMore(false); return; }
    const list = r.events || [];
    setRows(prev => before ? [...(prev || []), ...list] : list);
    setMore(list.length >= 60);
  };
  useEffect(() => { load(); }, [ctx.project(), ctx.boardVersion]);
  const b = ctx.board;
  const list = rows || [];
  return html`<section class="sec narrow">
    <div class="sec-head"><div class="sec-title"><h2 class="h2">Activity</h2><span class="sub">Audit trail of the desk, newest first.</span></div></div>
    <div class="card flat">${list.map(e => html`<div class="evt"><span class="t">${hm(e.created)}</span><span class="k" title=${e.kind}>${e.kind.replaceAll('.', ' ')}</span>
        <span class="a" style=${{color: `var(--${whoFor(b, e.actor).kind === 'none' ? 'mute' : whoFor(b, e.actor).kind})`}}>${e.actor_name || whoFor(b, e.actor).name}</span><span class="x">${e.text || describe(e)}</span></div>`)}
      ${rows === null ? html`<div class="empty">Loading…</div>` : list.length ? '' : html`<div class="empty">No activity.</div>`}
      ${more && list.length ? html`<div class="led-foot"><button type="button" class="btn" onClick=${() => load(list[list.length - 1].seq)}>Load older</button></div>` : ''}</div>
  </section>`;
}

export function describe(e) {
  const d = e.data || {};
  if (typeof d === 'string') return d;
  const bits = [d.task_id, d.title, d.status, d.service, d.commit_ref, d.message_id, d.recipient && `to ${d.recipient}`].filter(Boolean);
  return bits.length ? bits.join(' · ') : firstLine(JSON.stringify(d), 140);
}
