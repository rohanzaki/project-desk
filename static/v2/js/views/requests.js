import {html, useState, useEffect, useContext} from '../../vendor/preact-htm.module.js';
import {Desk} from '../context.js';
import * as api from '../api.js';
import {Seg, Btn} from '../ui.js';
import {Who, whoFor, when} from '../util.js';

const TABS = [['proposed', 'Waiting for you'], ['approved', 'Approved'], ['in_progress', 'Being built'], ['done', 'Done'], ['', 'All']];
const LABEL = {proposed: 'waiting for you', approved: 'approved · needs a volunteer', in_progress: 'being built', done: 'done', rejected: 'not approved', withdrawn: 'withdrawn'};

// Agents propose improvements to the desk; nobody builds one before the human approves.
export function DeskRequests() {
  const ctx = useContext(Desk);
  const [tab, setTab] = useState(ctx.ui.drTab ?? 'proposed');
  const [rows, setRows] = useState(null);
  useEffect(() => { let live = true; api.deskRequests(tab).then(r => live && setRows(r)).catch(() => live && setRows(null)); return () => { live = false; }; }, [tab, ctx.boardVersion]);
  const A = ctx.actions, b = ctx.board;
  return html`<section class="sec narrow">
    <div class="sec-head"><div class="sec-title"><h2 class="h2">Desk requests</h2><span class="sub">Agents ask for desk improvements. You approve; then any willing agent builds it.</span></div>
      <${Seg} tabs=${TABS} value=${tab} onPick=${k => { ctx.ui.drTab = k; setTab(k); }} /></div>
    <div class="card flat">
      ${rows === null ? html`<div class="empty">This desk has no request endpoint yet; it arrives with the dashboard v2 restart.</div>`
        : rows.length ? rows.map(r => html`<div class="note-row">
          <div class="hd"><span class=${`nk dr-${r.status}`}>${LABEL[r.status] || r.status}</span><${Who} w=${whoFor(b, r.author, {name: r.author_name})} /><span class="proj-tag">${r.origin_project}</span><span>${when(r.created)}</span><span class="mono muted2">${r.id}</span>
            ${r.support_count ? html`<span>· backed by ${r.support.map(s => s.name).join(', ')}</span>` : ''}</div>
          <div style=${{fontSize: '14px', fontWeight: 700, color: 'var(--ink2)'}}>${r.title}</div>
          <div class="bd">${r.why}</div>
          ${r.proposal ? html`<details><summary class="muted" style=${{fontSize: '12px', cursor: 'pointer'}}>Proposal</summary><div class="bd">${r.proposal}</div></details>` : ''}
          ${r.support.filter(s => s.note).map(s => html`<div class="muted" style=${{fontSize: '12px'}}>+ ${s.name}: ${s.note}</div>`)}
          ${r.decision_note ? html`<div class="muted" style=${{fontSize: '12px'}}>Your note: ${r.decision_note}</div>` : ''}
          ${r.volunteer ? html`<div class="muted" style=${{fontSize: '12px'}}>Built by ${r.volunteer_name} (${r.volunteer_project})${r.task_id ? html` · <button type="button" class="link-btn" onClick=${() => ctx.openTask(r.task_id, r.volunteer_project)}>${r.task_id} ${r.task_status}</button>` : ''}${r.done_commit ? ` · commit ${r.done_commit}` : ''}</div>` : ''}
          ${r.status === 'proposed' ? html`<div class="sec-actions"><${Btn} label="Approve" kind="p" onClick=${() => A.approveRequest(r)} /><${Btn} label="Approve with a note…" onClick=${() => A.approveRequest(r, true)} /><${Btn} label="Reject…" kind="d" onClick=${() => A.rejectRequest(r)} /></div>` : ''}
        </div>`) : html`<div class="empty">${tab === 'proposed' ? 'No requests waiting for you.' : 'Nothing here.'}</div>`}
    </div>
  </section>`;
}
