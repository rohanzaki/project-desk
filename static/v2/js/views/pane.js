import {html, useState, useEffect, useContext, useRef} from '../../vendor/preact-htm.module.js';
import {Desk} from '../context.js';
import * as api from '../api.js';
import {Btn} from '../ui.js';
import {Who, Av, Status, whoFor, taskOf, ago, hm, when, overlaps, evidenceOf, HUMAN} from '../util.js';

const claimNote = p => p.startsWith('service:') ? 'deploy lane' : (p.endsWith('/') || !/\.[a-z0-9]+$/i.test(p.split('/').pop())) ? 'dir · descendants' : 'file';

function useDetail(ctx, t) {
  const [detail, setDetail] = useState(null);
  useEffect(() => {
    let live = true;
    if (!t) { setDetail(null); return undefined; }
    api.taskDetail(ctx.project(), t.id).then(d => { if (live) setDetail(d); }).catch(() => { if (live) setDetail(null); });
    return () => { live = false; };
  }, [t && t.id, t && t.version, ctx.boardVersion]);
  return detail && detail.task && detail.task.id === (t && t.id) ? detail : null;
}

function Receipt({m}) {
  const acks = (m.acknowledgments || []).map(a => a.session_id).filter(id => id !== m.sender);
  const ctx = useContext(Desk);
  return acks.length
    ? html`<span class="rc-ok">Read by ${acks.map(id => whoFor(ctx.board, id).short).join(', ')}</span>`
    : html`<span class="rc-wait">Sent · awaiting read</span>`;
}

export function TaskPane({drawer}) {
  const ctx = useContext(Desk);
  const b = ctx.board;
  const t = taskOf(b, ctx.sel);
  const detail = useDetail(ctx, t);
  const [comment, setComment] = useState('');
  const box = useRef(null);
  if (!t) {
    return html`<aside class=${`pane${drawer ? ' drawer' : ''}`}><div class="pane-body"><p class="empty">Select a task to see its claims, evidence, journal and discussion.</p></div></aside>`;
  }
  const A = ctx.actions, w = whoFor(b, t.owner);
  const done = t.status === 'DONE', blocked = t.status === 'BLOCKED' || t.status === 'PAUSED';
  const journal = detail ? detail.journal : ((b.task_logs || {})[t.id] || []);
  const talk = (detail ? detail.messages : (b.messages || []).filter(m => m.task_id === t.id)).slice().sort((x, y) => String(x.created).localeCompare(String(y.created)));
  const items = (detail ? detail.action_items : (b.action_items || []).filter(i => i.task_id === t.id)).filter(i => i.status === 'open' || i.status === 'done');
  const openItems = items.filter(i => i.status === 'open');
  const lessons = detail ? detail.lessons : (b.lessons || []).filter(l => (l.paths || []).some(p => t.resources.some(r => overlaps(p, r))));
  const brief = (detail ? detail.handoff_briefs : (b.handoff_briefs || []).filter(h => h.task_id === t.id)).find(h => h.status === 'offered');
  const xo = detail ? detail.crossover : (b.crossovers || []).find(x => x.members.some(m => m.task_id === t.id));
  const ev = evidenceOf(b, t.id);
  const checks = [['validation', t.validation || 'not recorded'], ['commit', t.commit_ref || 'not recorded'], ['deployment', t.deployment || '—']];
  for (const e of (detail && detail.evidence) || []) {
    checks.push(['check', [e.command, e.exit_code !== null && e.exit_code !== undefined ? `exit ${e.exit_code}` : '', e.tests_passed != null ? `${e.tests_passed} passed · ${e.tests_failed || 0} failed` : '', e.commit_ref].filter(Boolean).join(' · ')]);
  }
  const send = () => { const body = comment.trim(); if (!body) return; A.comment(t, body); setComment(''); };
  const buttons = done ? [['Reopen & reassign', () => A.reopen(t), 'p']]
    : [[t.human_paused ? 'Resume' : 'Pause', () => (t.human_paused ? A.resume(t) : A.pause(t)), t.human_paused ? 'p' : ''],
       ['Reassign', () => A.reassign(t)], ['Set priority', () => A.priority(t)], ['Cross over', () => A.crossover(t)],
       ['Close with note', () => A.close(t), 'd']];
  return html`<aside class=${`pane${drawer ? ' drawer' : ''}`} aria-label="Task details">
    <div class="pane-head">
      <div class="pane-meta">
        ${drawer ? html`<button type="button" class="x-btn" aria-label="Close" onClick=${() => ctx.closePane()}>✕</button>` : ''}
        <span class="id">${t.id}</span><${Status} s=${t.status} />
        ${t.priority && t.priority !== 'normal' ? html`<span class="pr">${t.priority}</span>` : ''}
        ${t.human_paused ? html`<span class="pby">paused by you</span>` : ''}
        <span class="upd">updated ${ago(t.updated)}</span>
      </div>
      <h3 class="h3">${t.title}</h3>
      <div class="pane-who"><${Who} w=${w} full />${w.branch ? html`<span class="mono">${w.branch}</span>` : ''}${t.assigned_to && !t.owner ? html`<span>queued for ${t.assigned_to}</span>` : ''}
        <button type="button" class=${`pin${ctx.pins.has(t.id) ? ' on' : ''}`} onClick=${() => ctx.togglePin(t.id)}>${ctx.pins.has(t.id) ? '★ pinned' : '☆ pin'}</button></div>
      <div class="pane-btns">${buttons.map(([l, fn, k]) => html`<${Btn} label=${l} kind=${k || ''} onClick=${fn} />`)}</div>
    </div>
    <div class="pane-body">
      <div class=${`next${done ? ' done' : blocked ? ' blocked' : ''}`}><div class="l">${done ? 'Outcome' : blocked ? 'Blocked · next step' : 'Next step'}</div>
        <div class="v">${done ? (t.summary || 'No completion summary recorded') : (t.next_step || 'No next step recorded')}</div></div>

      ${brief ? html`<div class="handoff"><div class="l">Handoff offered to ${whoFor(b, brief.target_session).name}</div>${brief.remaining_work || brief.progress || ''}</div>`
        : t.pending_owner ? html`<div class="handoff"><div class="l">Handoff pending</div>Offered to ${whoFor(b, t.pending_owner).name}; awaiting acceptance.</div>` : ''}

      ${openItems.length || items.length ? html`<div class="blk"><div class="blk-h"><span>Action points</span>
          ${openItems.length ? html`<span class="r"><button type="button" class="link-btn" onClick=${() => A.clearItems(ctx.project(), openItems.map(i => i.id), 'this task')}>Clear all</button></span>` : ''}</div>
        <div class="box">${items.map(i => html`<label class=${`ai${i.status !== 'open' ? ' done' : ''}`}>
          <input type="checkbox" checked=${i.status !== 'open'} onChange=${e => A.tick(ctx.project(), [i.id], e.target.checked ? 'done' : 'open')} />
          <span class="b">${i.body}<span class="m">for ${i.assignee === HUMAN ? 'you' : i.assignee === 'agents' ? 'any agent' : whoFor(b, i.assignee).short} · ${i.author_name || whoFor(b, i.author).short} · ${when(i.created)}</span></span></label>`)}</div></div>` : ''}

      <div class="blk"><div class="blk-h"><span>Claims</span><span class="r">${done ? 'released on DONE' : 'refused to others while held'}</span></div>
        <div class="box">${t.resources.length ? t.resources.map(p => html`<div class=${`claim${done ? ' rel' : ''}`}><span class="p">${p}</span><span class=${`note${p.startsWith('service:') && !done ? ' lane' : ''}`}>${done ? 'released' : claimNote(p)}</span></div>`)
          : html`<div class="claim"><span class="p muted2">No paths</span></div>`}</div></div>

      <div class="blk"><div class="blk-h"><span>Evidence</span><span class=${`r ev ${ev.cls}`}>${ev.text}</span></div>
        <div class="box kv">${checks.map(([k, v]) => html`<span>${k}</span><span>${v}</span>`)}</div></div>

      ${lessons.length ? html`<div>${lessons.slice(0, 4).map(l => html`<div class="lesson-box"><div class="l">Lesson on these paths</div>${l.body}${l.author_name ? ` — ${l.author_name}` : ''}</div>`)}</div>` : ''}

      ${xo ? html`<div class="blk"><div class="blk-h"><span>Crossover ${xo.id}</span><span class="r">${xo.status}</span></div>
        <div class="box">${xo.members.map(m => html`<div class="claim"><span class="p">${m.project}${m.task_id ? ` · ${m.task_id} · ${m.task_status || ''}` : ' · invited, not joined'}</span><span class="note">${m.signed_off ? 'signed off' : 'awaiting sign-off'}</span></div>`)}</div>
        <button type="button" class="link-btn" onClick=${() => A.crossoverLink(xo)}>Join link</button></div>` : ''}

      <div class="blk"><div class="blk-h"><span>Journal</span></div>
        <div class="journal">${journal.length ? journal.slice(-40).map(j => html`<div class="jr"><span class="t">${hm(j.created)}</span><span class="k">${j.kind}</span><span class="x">${j.entry}</span></div>`)
          : html`<div class="jr"><span></span><span></span><span class="x muted2">No journal entries.</span></div>`}</div></div>

      <div class="blk"><div class="blk-h"><span>Discussion</span></div>
        ${talk.map(m => { const mw = whoFor(b, m.sender, {name: m.from_name}); return html`<div class="talk"><${Av} w=${mw} /><div class="m">
          <div class="hd"><strong>${mw.name}</strong> · ${when(m.created)} · <${Receipt} m=${m} /></div><div class="bd">${m.body}</div></div></div>`; })}
        ${talk.length ? '' : html`<div class="muted2" style=${{fontSize: '11px'}}>No comments yet.</div>`}
        <div class="composer">
          <textarea ref=${box} rows="2" placeholder=${`Comment to ${w.name}…`} value=${comment} onInput=${e => setComment(e.target.value)}
            onKeyDown=${e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send(); } }} aria-label="Comment"></textarea>
          <div class="row"><span class="hint">⌘⏎ to send · a receipt confirms reading, not approval</span><${Btn} label="Send" kind="p" onClick=${send} /></div>
        </div>
      </div>
    </div>
  </aside>`;
}
