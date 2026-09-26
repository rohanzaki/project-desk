import {html, useContext} from '../../vendor/preact-htm.module.js';
import {Desk} from '../context.js';
import {Seg} from '../ui.js';
import {Who, Status, whoFor, ago, evidenceOf, store, pinKey} from '../util.js';

const STATUS_TABS = [['active', 'Active'], ['all', 'All'], ['RUNNING', 'Running'], ['BLOCKED', 'Blocked'], ['PAUSED', 'Paused'], ['QUEUED', 'Queued'], ['DONE', 'Done']];
const inTab = (t, tab) => tab === 'all' || (tab === 'active' ? t.status !== 'DONE' : t.status === tab);

function Row({t, wide}) {
  const ctx = useContext(Desk);
  const b = ctx.board, w = whoFor(b, t.owner), ev = evidenceOf(b, t.id);
  const pinned = ctx.pins.has(t.id);
  const xo = (b.crossovers || []).find(x => x.status !== 'closed' && x.members.some(m => m.task_id === t.id));
  const next = t.status === 'DONE' ? (t.summary || 'No completion summary') : (t.next_step || 'No next step recorded');
  return html`<div class=${`led-row${ctx.sel === t.id ? ' sel' : ''}`} onClick=${() => ctx.select(t.id, {open: true})} role="button" tabindex="0"
      onKeyDown=${e => { if (e.key === 'Enter') ctx.select(t.id, {open: true}); }}>
    <span class=${`bar bar-${t.status}`}></span>
    <button type="button" class=${`pin${pinned ? ' on' : ''}`} aria-label=${pinned ? 'Unpin' : 'Pin'} title=${pinned ? 'Unpin' : 'Pin to top'} onClick=${e => { e.stopPropagation(); ctx.togglePin(t.id); }}>${pinned ? '★' : '☆'}</button>
    <span class="st-cell"><${Status} s=${t.status} /></span>
    <div style=${{minWidth: 0, display: 'flex', flexDirection: 'column', gap: '3px'}}>
      <div class="ttl"><span class="t" title=${t.title}>${t.title}</span>
        ${t.priority && t.priority !== 'normal' ? html`<span class="pr">${t.priority}</span>` : ''}
        ${xo ? html`<span class="xo-tag">${xo.id}</span>` : ''}
        ${t.human_paused ? html`<span class="pr">paused by you</span>` : ''}</div>
      <div class="nx" title=${next}>${next}</div>
    </div>
    <${Who} w=${w} />
    ${wide ? html`<div class="paths wide"><span title=${t.resources.join('\n')}>${t.resources[0] || '—'}</span>${t.resources.length > 1 ? html`<span class="more">+${t.resources.length - 1}</span>` : ''}</div>
      <span class=${`ev wide ${ev.cls}`}>${ev.text}</span>
      <span class="upd wide">${ago(t.updated)}</span>` : ''}
  </div>`;
}

export function Ledger({mode}) {
  const ctx = useContext(Desk);
  const b = ctx.board;
  const f = ctx.ui.ledger || (ctx.ui.ledger = {q: '', owner: 'all', tab: 'active'});
  const q = f.q.trim().toLowerCase();
  const match = t => (f.owner === 'all' || t.owner === f.owner || (f.owner === 'unassigned' && !t.owner))
    && (!q || [t.title, t.id, whoFor(b, t.owner).name, t.resources.join(' '), t.next_step, t.summary].join(' ').toLowerCase().includes(q));
  const pool = (b.tasks || []).filter(match);
  const visible = mode === 'command' ? pool : pool.filter(t => inTab(t, f.tab));
  const pinned = visible.filter(t => ctx.pins.has(t.id));
  const rest = visible.filter(t => !ctx.pins.has(t.id));
  const groups = [
    ['Pinned', pinned],
    ['Running', rest.filter(t => t.status === 'RUNNING')],
    ['Blocked & paused', rest.filter(t => t.status === 'BLOCKED' || t.status === 'PAUSED')],
    ['Queued', rest.filter(t => t.status === 'QUEUED')],
    ['Done', rest.filter(t => t.status === 'DONE')],
  ].filter(([, rows]) => rows.length);
  ctx.ui.visible = groups.flatMap(([, rows]) => rows);
  const wide = ctx.mainWidth >= 760;
  const openN = pool.filter(t => t.status !== 'DONE').length, doneN = pool.filter(t => t.status === 'DONE').length;
  const doneTotal = b.tasks_done_total ?? null, doneShown = b.tasks_done_shown ?? doneN;
  const owners = [{v: 'all', l: 'All owners'}, {v: 'unassigned', l: 'Unassigned'},
    ...[...new Set((b.tasks || []).map(t => t.owner).filter(Boolean))].map(id => ({v: id, l: whoFor(b, id).name}))];
  const set = patch => { Object.assign(f, patch); ctx.rerender(); };
  return html`<section class="sec">
    <div class="sec-head">
      <div class="sec-title"><h2 class="h2">${mode === 'command' ? 'Work' : 'Tasks'}</h2>
        <span class="n">${mode === 'command' ? `${openN} open · ${doneN} done${ctx.doneDays < 3650 ? ' (7 days)' : ''}` : `${visible.length} shown`}</span></div>
      <div class="sec-actions">
        <input class="input" style=${{width: '240px'}} placeholder="Title, owner, next step or path" value=${f.q} onInput=${e => set({q: e.target.value})} aria-label="Filter tasks" />
        <select class="input" value=${f.owner} onChange=${e => set({owner: e.target.value})} aria-label="Owner">${owners.map(o => html`<option value=${o.v}>${o.l}</option>`)}</select>
        <button type="button" class="btn lg p" onClick=${() => ctx.actions.newTask()}>Add task</button>
      </div>
    </div>
    ${mode === 'tasks' ? html`<${Seg} start tabs=${STATUS_TABS.map(([k, l]) => [k, l, pool.filter(t => inTab(t, k)).length])} value=${f.tab} onPick=${k => set({tab: k})} />` : ''}
    <div class=${`card led${wide ? '' : ' narrow'}`}>
      <div class="led-head"><span></span><span class="st-cell">Status</span><span>Task · next step</span><span>Owner</span>${wide ? html`<span class="wide">Claims</span><span class="wide">Evidence</span><span class="wide" style=${{textAlign: 'right'}}>Upd.</span>` : ''}</div>
      ${groups.map(([label, rows]) => html`
        <div class="led-group"><span>${label}</span><span class="c">${rows.length}</span></div>
        ${rows.slice(0, label === 'Done' && !ctx.ui.allDoneRows ? 60 : 400).map(t => html`<${Row} key=${t.id} t=${t} wide=${wide} />`)}`)}
      ${groups.length ? '' : html`<div class="empty">No tasks match this view. Clear a filter or add a task.</div>`}
      ${(doneTotal !== null && doneTotal > doneShown && ctx.doneDays < 3650) || (groups.some(([l, r]) => l === 'Done' && r.length > 60) && !ctx.ui.allDoneRows)
        ? html`<div class="led-foot"><button type="button" class="btn" onClick=${() => { ctx.ui.allDoneRows = true; ctx.setDoneDays(3650); }}>Show all done${doneTotal !== null ? ` (${doneTotal})` : ''}</button></div>` : ''}
    </div>
  </section>`;
}
