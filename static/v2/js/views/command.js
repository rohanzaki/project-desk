import {html, useState, useEffect, useContext} from '../../vendor/preact-htm.module.js';
import {Desk} from '../context.js';
import {Seg, Btn} from '../ui.js';
import {Who, Av, whoFor, taskOf, ago, hm, when, firstLine, snoozeTimes, HUMAN} from '../util.js';

const KINDS = [['all', 'All'], ['approval', 'Approvals'], ['question', 'Questions'], ['blocked', 'Blocked on you'], ['desk_request', 'Desk requests'],
  ['stale', 'Stale'], ['refused', 'Refused'], ['action', 'Action points']];
const TYPE = {approval: 'Approval', question: 'Question', blocked: 'Blocked on you', stale: 'Stale claim', refused: 'Refused claim', action: 'Action points', desk_request: 'Desk request'};

// Buttons per kind. Numbers are the 1–9 shortcut keys; E ticks, S snoozes.
export function itemActions(ctx, item) {
  const A = ctx.actions, open = () => item.task_id && ctx.openTask(item.task_id, item.project);
  const out = [];
  if (item.kind === 'approval') {
    (item.options || []).forEach((o, i) => out.push({label: o, kind: i === 0 ? 'p' : '', run: () => A.decide(item, o)}));
    out.push({label: 'Other…', run: () => A.otherDecision(item)});
  } else if (item.kind === 'question') {
    out.push({label: 'Yes, go ahead', kind: 'p', run: () => A.answer(item, 'Yes, go ahead.', false)});
    out.push({label: 'Answer…', run: () => A.answerDialog(item)});
  } else if (item.kind === 'blocked') {
    out.push({label: 'Open task', kind: 'p', run: open});
    out.push({label: 'Reply…', run: () => ctx.actions.message(item.who && item.who.id || 'all', item.task_id, '', item.who && item.who.id, item.who && item.who.name)});
  } else if (item.kind === 'stale') {
    const t = taskOf(ctx.board, item.task_id) || {id: item.task_id, version: item.version, resources: item.resources || []};
    out.push({label: 'Reassign…', run: () => item.project === ctx.project() ? A.reassign(t) : ctx.openTask(item.task_id, item.project)});
    out.push({label: 'Close…', kind: 'd', run: () => item.project === ctx.project() ? A.close(t) : ctx.openTask(item.task_id, item.project)});
  } else if (item.kind === 'refused') {
    out.push({label: 'Queue it', run: () => A.refusal(item, 'queue')});
    out.push({label: 'Ask handoff', run: () => A.refusal(item, 'handoff')});
    out.push({label: 'Dismiss', run: () => A.refusal(item, 'dismiss')});
  } else if (item.kind === 'desk_request') {
    out.push({label: 'Approve', kind: 'p', run: () => A.approveRequest(item)});
    out.push({label: 'Approve with note…', run: () => A.approveRequest(item, true)});
    out.push({label: 'Reject…', kind: 'd', run: () => A.rejectRequest(item)});
  } else if (item.kind === 'action') {
    const ids = item.item_ids || [];
    out.push({label: ids.length > 1 ? 'Tick all' : 'Tick', key: 'E', run: () => A.tick(item.project, ids, 'done')});
    out.push({label: 'Clear', run: () => A.clearItems(item.project, ids, item.task_title)});
  }
  return out.map((b, i) => ({...b, key: b.key || String(i + 1)}));
}

// The server sends raw facts; this turns each kind into a title and one meta line.
export function present(item) {
  const n = (item.item_ids || []).length;
  switch (item.kind) {
    case 'action': return {title: (item.bodies && item.bodies.length ? item.bodies.join(' · ') : item.title),
      meta: `${n || 1} left for you on ${item.task_title || 'no task'}`};
    case 'stale': return {title: item.task_title || item.title,
      meta: `owner silent ${ago(item.owner_last_seen)} · still holds ${(item.resources || []).join(', ')} · claims never expire on their own`};
    case 'refused': return {title: `Wanted ${(item.resources || []).join(', ')} for “${item.title}”`,
      meta: `held by ${item.holder_name || item.holder_session || 'another task'}${item.holder_task_id ? ' on ' + item.holder_task_id : ''} · refused, not merged`};
    case 'blocked': return {title: item.task_title || item.title, meta: item.next_step || item.meta || ''};
    case 'question': return {title: item.body ? firstLine(item.body, 240) : item.title, meta: item.task_title || item.meta || ''};
    case 'desk_request': return {title: item.title, meta: item.meta || ''};
    default: return {title: item.title, meta: item.meta || item.task_title || ''};
  }
}

function SnoozeMenu({item, onDone}) {
  const {actions} = useContext(Desk);
  return html`<div class="snooze-menu" role="menu" onClick=${e => e.stopPropagation()}>
    ${snoozeTimes().map(([label, until]) => html`<button type="button" role="menuitem" onClick=${() => { actions.snooze(item, until); onDone(); }}>Snooze · ${label}</button>`)}
  </div>`;
}

function AttRow({item, i, cur, multi}) {
  const ctx = useContext(Desk);
  const [menu, setMenu] = useState(false);
  useEffect(() => { if (ctx.ui.snoozeFor === item.key) { setMenu(true); ctx.ui.snoozeFor = null; } });
  const w = item.who ? whoFor(ctx.board, item.who.id, item.who) : whoFor(ctx.board, '');
  const acts = itemActions(ctx, item);
  const shown = present(item);
  return html`<div class=${`att-row${cur ? ' cur' : ''}`} onClick=${() => { ctx.setCursor(i); if (item.task_id && item.project === ctx.project()) ctx.select(item.task_id); }}>
    <div class=${`att-type k-${item.kind}`}><span class="sq"></span>${TYPE[item.kind] || item.kind}</div>
    <div class="att-main">
      <div class="att-title" title=${shown.title}>${shown.title}</div>
      <div class="att-meta">${multi ? html`<span class="proj-tag">${item.project}</span>` : ''}<${Who} w=${w} /><span class="t" title=${shown.meta}>${shown.meta}</span><span>${when(item.created)}</span></div>
    </div>
    <div class="att-acts">
      ${acts.map(b => html`<${Btn} label=${b.label} kind=${b.kind || ''} k=${b.key} onClick=${b.run} />`)}
      <${Btn} label="Snooze" k="S" onClick=${() => setMenu(!menu)} title="Hide it until later" />
      ${menu ? html`<${SnoozeMenu} item=${item} onDone=${() => setMenu(false)} />` : ''}
    </div>
  </div>`;
}

export function NeedsYou() {
  const ctx = useContext(Desk);
  const all = ctx.attention.items || [];
  const kind = ctx.ui.attKind || 'all';
  const items = all.filter(a => kind === 'all' || a.kind === kind);
  ctx.ui.att = items;
  const cursor = Math.min(ctx.cursor, Math.max(0, items.length - 1));
  const multi = new Set(all.map(a => a.project)).size > 1 || all.some(a => a.project !== ctx.project());
  const tabs = KINDS.map(([k, l]) => [k, l, k === 'all' ? all.length : all.filter(a => a.kind === k).length])
    .filter(([k, , n]) => k === 'all' || n || k === kind);
  const others = Object.entries(ctx.attention.counts || {}).filter(([p, n]) => p !== ctx.project() && n);
  return html`<section class="sec">
    <div class="sec-head">
      <div class="sec-title"><h2 class="h2">Needs you</h2><span class="n">${all.length}</span>
        ${others.length ? html`<span class="sub">incl. ${others.map(([p, n]) => `${n} in ${p}`).join(' · ')}</span>` : ''}</div>
      <${Seg} tabs=${tabs} value=${kind} onPick=${k => { ctx.ui.attKind = k; ctx.setCursor(0); ctx.rerender(); }} />
    </div>
    <div class="card">
      ${items.map((a, i) => html`<${AttRow} key=${a.key} item=${a} i=${i} cur=${i === cursor} multi=${multi} />`)}
      ${items.length ? '' : html`<div class="empty">Nothing waiting on you. Agents keep working; new approvals, questions, blocked tasks and stale claims land here.</div>`}
      ${ctx.attention.snoozed ? html`<div class="att-foot"><span>${ctx.attention.snoozed} snoozed item(s) hidden</span><button type="button" class="link-btn" onClick=${() => ctx.toggleSnoozed()}>${ctx.attention.showingSnoozed ? 'Hide snoozed' : 'Show snoozed'}</button></div>` : ''}
      ${ctx.attention.local ? html`<div class="att-foot"><span>This project only: the desk has no cross-project attention endpoint yet.</span></div>` : ''}
    </div>
  </section>`;
}

export function SinceStrip() {
  const ctx = useContext(Desk);
  const [open, setOpen] = useState(false);
  const [all, setAll] = useState(false);
  const d = ctx.digest;
  if (!d || !ctx.lookedSince) return null;
  const parts = [['done', 'finished'], ['deploys', 'deployed'], ['claimed', 'started'], ['questions', 'questions'], ['approvals', 'approvals'], ['decisions', 'decisions']]
    .map(([k, l]) => [k, l, (d.counts && d.counts[k]) ?? (d[k] || []).length]).filter(([, , n]) => n);
  if (!parts.length && !d.messages_to_you) return null;
  const rows = [];
  for (const t of d.done || []) rows.push(['done', t.title, t.owner_name, t.updated, t.task_id]);
  for (const x of d.deploys || []) rows.push(['deploy', `${x.service} at ${x.commit_ref}${x.summary ? ' — ' + firstLine(x.summary, 90) : ''}`, x.session_name, x.created, x.task_id]);
  for (const t of d.claimed || []) rows.push(['started', t.title, t.owner_name, t.created || t.updated, t.task_id]);
  for (const q of [...(d.questions || []), ...(d.approvals || [])]) rows.push(['asked', firstLine(q.body || q.title, 110), q.asker_name || q.requester_name, q.created, q.task_id]);
  for (const n of d.decisions || []) rows.push([n.kind || 'note', firstLine(n.body, 110), n.author_name, n.created]);
  return html`<div>
    <div class="since">
      <strong>Since ${when(ctx.lookedSince)}</strong>
      ${parts.map(([, l, n]) => html`<span class="pill">${n} ${l}</span>`)}
      ${d.messages_to_you ? html`<span class="pill">${d.messages_to_you} message(s) to you</span>` : ''}
      <span class="grow"></span>
      <button type="button" class="link-btn" onClick=${() => setOpen(!open)}>${open ? 'Hide' : 'Show what'}</button>
      <button type="button" class="link-btn" onClick=${() => ctx.markLooked()}>Mark seen</button>
    </div>
    ${open ? html`<div class="since-list">${rows.slice(0, all ? 60 : 12).map(([k, t, who, at, tid]) => html`
      <div class="li"><span class="k">${k}</span><span>${tid ? html`<button type="button" class="link-btn" onClick=${() => ctx.openTask(tid)}>${t}</button>` : t}${who ? html` <span class="muted">· ${who}</span>` : ''}</span><span class="mono muted2">${when(at)}</span></div>`)}${rows.length > 12 && !all ? html`<div class="more"><button type="button" class="link-btn" onClick=${() => setAll(true)}>Show all ${rows.length}</button></div>` : ''}</div>` : ''}
  </div>`;
}

export function OnDesk() {
  const ctx = useContext(Desk);
  const [showAll, setShowAll] = useState(false);
  const b = ctx.board;
  const openTasks = (b.tasks || []).filter(t => t.status !== 'DONE');
  const ownerOf = new Map();
  for (const t of openTasks) if (t.owner && !ownerOf.has(t.owner)) ownerOf.set(t.owner, t);
  const sessions = (b.sessions || []).filter(s => !s.imported);
  const live = sessions.filter(s => !s.stale);
  const staleHolding = sessions.filter(s => s.stale && ownerOf.has(s.id));
  const rest = sessions.filter(s => s.stale && !ownerOf.has(s.id));
  const shown = [...live, ...staleHolding, ...(showAll ? rest.slice(0, 60) : [])];
  return html`<section class="sec">
    <div class="sec-head">
      <div class="sec-title"><h2 class="h2 sm">On the desk</h2><span class="sub">${live.length} checked in${staleHolding.length ? ` · ${staleHolding.length} stale holding claims (claims stay reserved)` : ''}</span></div>
      <button type="button" class="link-btn" onClick=${() => ctx.go('lanes')}>Agent lanes →</button>
    </div>
    <div class="sess-grid">
      ${shown.map(s => {
        const w = whoFor(b, s.id), t = ownerOf.get(s.id), wait = t && t.status === 'BLOCKED';
        const cls = s.stale ? 'stale' : wait ? 'wait' : '';
        return html`<button type="button" class=${`sess ${cls}`} onClick=${() => t && ctx.select(t.id, {open: true})} title=${`${s.name}\n${s.branch || ''}\n${s.worktree || ''}`}>
          <${Av} w=${w} />
          <span class="txt"><span class="nm">${s.name}</span><span class="doing">${t ? (wait ? 'Waiting on you · ' : '') + t.title : 'No open task'}</span></span>
          <span class="age">${ago(s.last_seen)}<b>${s.stale ? 'stale' : wait ? 'waiting' : 'live'}</b></span>
        </button>`;
      })}
    </div>
    ${rest.length ? html`<button type="button" class="link-btn" onClick=${() => setShowAll(!showAll)}>${showAll ? 'Hide older sessions' : `${rest.length} older sessions without open work`}</button>` : ''}
  </section>`;
}
