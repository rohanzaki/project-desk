import {html, useState, useEffect, useContext} from '../../vendor/preact-htm.module.js';
import {Desk} from '../context.js';
import * as api from '../api.js';
import {Btn} from '../ui.js';
import {Who, Av, whoFor, taskOf, ago, hm, when, overlaps} from '../util.js';

// ---- Claims & locks ----------------------------------------------------------------
export function Claims() {
  const ctx = useContext(Desk);
  const b = ctx.board;
  const [probe, setProbe] = useState(ctx.ui.probe || '');
  const open = (b.tasks || []).filter(t => t.status !== 'DONE');
  const held = open.flatMap(t => t.resources.map(p => ({p, t}))).sort((x, y) => x.p.localeCompare(y.p));
  const stale = new Set((b.stale_claims || []).map(s => s.task_id));
  const refusedPaths = new Map((b.refusals || []).flatMap(r => (r.resources || []).map(p => [p, r])));
  const cq = probe.trim();
  const hit = cq ? held.find(({p}) => overlaps(p, cq)) : null;
  const verdict = !cq ? ['', 'Type a path to check before you plan around it.', '']
    : hit ? ['Held', ` by ${whoFor(b, hit.t.owner).name} through ${hit.p === cq ? 'the claim' : 'the claim'} ${hit.p} on ${hit.t.id} (${hit.t.title}). A claim here would be refused.`, 'held']
    : cq.startsWith('service:') && (b.shared_locks || []).some(l => l.resource === cq) ? ['Held', ' in another project. A claim here would be refused.', 'held']
    : ['Free', ' No open task in this project holds this path. A claim would succeed.', 'free'];
  // Deploy records name a service without the 'service:' prefix; claims and queues use it.
  const services = new Map(), svc = name => 'service:' + String(name).replace(/^service:/, '');
  const entry = name => { const k = svc(name); if (!services.has(k)) services.set(k, {service: k}); return services.get(k); };
  for (const s of b.prod || []) entry(s.service).prod = s;
  for (const q of b.queues || []) entry(q.resource).queue = q.queue;
  for (const {p, t} of held) if (p.startsWith('service:')) entry(p).holder = t;
  const lanes = [...services.values()].sort((x, y) => (y.holder ? 1 : 0) - (x.holder ? 1 : 0) || x.service.localeCompare(y.service));
  return html`<section class="claims-layout">
    <div class="sec">
      <div class="sec-title"><h2 class="h2">Claims & locks</h2><span class="sub">${held.length} paths held by ${new Set(held.map(h => h.t.owner)).size} sessions · directory claims cover descendants</span></div>
      <div class="card flat">${held.map(({p, t}) => {
        const warn = stale.has(t.id) || refusedPaths.has(p);
        return html`<div class=${`cl-row${warn ? ' warn' : ''}`} onClick=${() => ctx.select(t.id, {open: true, view: 'tasks'})} title=${t.title}>
          <span class="p">${p}</span><${Who} w=${whoFor(b, t.owner)} /><span class="n">${stale.has(t.id) ? 'stale' : refusedPaths.has(p) ? 'refused ' + hm(refusedPaths.get(p).created) : t.id}</span></div>`;
      })}${held.length ? '' : html`<div class="empty">No paths are held.</div>`}</div>
    </div>
    <aside class="side">
      <div class="sec"><div class="h">Would it conflict?</div>
        <input class="input mono" style=${{height: '36px'}} placeholder="path/to/file.ts or service:name" value=${probe}
          onInput=${e => { setProbe(e.target.value); ctx.ui.probe = e.target.value; }} aria-label="Path to check" />
        <div class=${`verdict ${verdict[2]}`}>${verdict[0] ? html`<strong>${verdict[0]}</strong>` : ''}${verdict[1]}</div></div>
      <div class="sec"><div class="h">Service lanes</div>
        ${lanes.slice(0, 20).map(l => html`<div class="lane-card">
          <div class="r"><span class="s">${l.service}</span><span class="m">${l.prod ? `prod ${String(l.prod.commit_ref || '').slice(0, 8)} · ${ago(l.prod.created)}` : ''}</span></div>
          ${l.holder ? html`<div class="r"><span class="held">Held · ${l.holder.title.slice(0, 60)}</span><${Who} w=${whoFor(b, l.holder.owner)} /></div>` : html`<span class="free">Free${l.prod && l.prod.session_name ? ` · last by ${l.prod.session_name}` : ''}</span>`}
          ${(l.queue || []).length ? html`<div class="m">Queue: ${(l.queue || []).map(q => q.name || q.session_id).join(' → ')} · the head gets YOUR TURN and 15 min to claim</div>` : ''}
        </div>`)}
        ${lanes.length ? '' : html`<div class="verdict">No service claims or deploys recorded.</div>`}</div>
      ${(b.shared_locks || []).length ? html`<div class="sec"><div class="h">Held in other projects</div>
        ${(b.shared_locks || []).map(l => html`<div class="other-lock"><span class="s">${l.resource}</span><span class="o">${l.project} · ${l.task_id.slice(-6)}</span></div>`)}</div>` : ''}
    </aside>
  </section>`;
}

// ---- Agent lanes -------------------------------------------------------------------
// Bars of one session that overlap in time share the track as two thin rows.
function stack(bars) {
  const sorted = bars.slice().sort((a, b) => String(a.start).localeCompare(String(b.start)));
  const overlap = sorted.some((b, i) => i && String(b.start) < String(sorted[i - 1].end));
  if (!overlap) return sorted.map(b => ({...b, row: null}));
  const ends = ['', ''];
  return sorted.map(b => { const row = String(b.start) >= ends[0] ? 0 : String(b.start) >= ends[1] ? 1 : (ends[0] <= ends[1] ? 0 : 1); ends[row] = String(b.end); return {...b, row}; });
}
export function Lanes() {
  const ctx = useContext(Desk);
  const [hours, setHours] = useState(12);
  const [data, setData] = useState(undefined);
  useEffect(() => { let live = true; setData(undefined); api.lanes(ctx.project(), hours).then(d => live && setData(d)).catch(() => live && setData(null)); return () => { live = false; }; }, [ctx.project(), hours, ctx.boardVersion]);
  const b = ctx.board;
  const head = html`<div class="sec-head"><div class="sec-title"><h2 class="h2">Agent lanes</h2>
    <span class="sub">${data ? `${when(data.start)} – ${when(data.end)} · click a bar to open the task` : ''}</span></div>
    <div class="seg">${[6, 12, 24, 72].map(h => html`<button type="button" class=${hours === h ? 'on' : ''} onClick=${() => setHours(h)}>${h} h</button>`)}</div></div>`;
  if (data === undefined) return html`<section class="sec">${head}<div class="empty">Loading…</div></section>`;
  if (data === null) return html`<section class="sec">${head}<div class="card flat"><div class="empty">This desk has no lanes endpoint yet; it arrives with the dashboard v2 restart.</div></div></section>`;
  const t0 = Date.parse(data.start), t1 = Date.parse(data.end), span = Math.max(1, t1 - t0);
  const pc = iso => `${Math.max(0, Math.min(100, (Date.parse(iso) - t0) / span * 100)).toFixed(2)}%`;
  const ticks = [0, .25, .5, .75, 1].map(f => ({l: f === 0 ? '3%' : f === 1 ? '96%' : `${f * 100}%`, label: hm(new Date(t0 + f * span).toISOString())}));
  const sym = {deploy: 'D', question: '?', approval: '?', refused: '✕', decision: '!', handoff: 'H'};
  return html`<section class="sec">${head}
    <div class="lanes">
      <div class="hc">Session</div>
      <div class="ticks">${ticks.map(k => html`<span style=${{left: k.l}}>${k.label}</span>`)}</div>
      ${(data.sessions || []).map(s => { const w = whoFor(b, s.id, s); return html`
        <div class=${`lane-who${s.stale ? ' stale' : ''}`}><${Av} w=${w} /><div style=${{minWidth: 0}}><div class="nm" title=${s.name}>${s.name}</div><div class="br">${s.branch || ''}</div></div></div>
        <div class="lane-track">
          ${stack(s.bars || []).map(x => { const l = pc(x.start), r = pc(x.end); return html`<div class=${`lane-bar lb-${x.kind} ${w.kind}${x.row === null ? '' : ' r' + x.row}`} title=${`${x.title} (${x.kind})`}
            style=${{left: l, width: `calc(${r} - ${l})`}} onClick=${() => x.task_id && ctx.select(x.task_id, {open: true, view: 'tasks'})}>${x.title}</div>`; })}
          ${(s.marks || []).map(m => html`<span class=${`lane-mark lm-${m.kind}`} title=${`${hm(m.t)} ${m.text}`} style=${{left: pc(m.t)}}>${sym[m.kind] || '•'}</span>`)}
          <div class="lane-now" style=${{left: pc(data.end)}}></div>
        </div>`; })}
    </div>
    ${(data.sessions || []).length ? '' : html`<div class="empty">No agent activity in this window.</div>`}
    <div class="legend">
      <span><i style=${{borderColor: 'var(--blue)', background: 'var(--blue-soft)'}}></i>Claim held</span>
      <span><i style=${{borderColor: 'var(--sage)', background: 'var(--sage-soft)'}}></i>Done</span>
      <span><i style=${{borderColor: 'var(--amber)', background: 'var(--amber-soft)', borderStyle: 'dashed'}}></i>Blocked / paused</span>
      <span><i style=${{borderColor: '#9aa6a0', background: 'var(--none-soft)', borderStyle: 'dashed'}}></i>Stale owner</span>
      <span><span class="lane-mark lm-deploy" style=${{position: 'static', transform: 'none'}}>D</span>Deploy</span>
      <span><span class="lane-mark lm-question" style=${{position: 'static', transform: 'none'}}>?</span>Question / approval</span>
      <span><span class="lane-mark lm-refused" style=${{position: 'static', transform: 'none'}}>✕</span>Refused claim</span>
    </div>
  </section>`;
}

// ---- Projects --------------------------------------------------------------------
export function Projects() {
  const ctx = useContext(Desk);
  const A = ctx.actions;
  const counts = ctx.attention.counts || {};
  const list = ctx.projects || [];
  return html`<section class="sec">
    <div class="sec-head"><div class="sec-title"><h2 class="h2">Projects</h2><span class="sub">Every project on this desk. Agents work only in the project their repo declares.</span></div>
      <div class="sec-actions"><button type="button" class="btn lg" onClick=${() => A.announce()}>Announce restart</button>
        <button type="button" class="btn lg p" onClick=${() => A.newProject()}>New project</button></div></div>
    <div class="proj-grid">${list.map(p => html`<div class=${`proj-card${p.slug === ctx.project() ? ' cur' : ''}`}>
      <div class="hd"><span class=${`dot${p.live_sessions ? '' : ' off'}`}></span><span class="nm">${p.name}</span><span class="sl">${p.slug}</span>${p.archived ? html`<span class="proj-tag">archived</span>` : ''}</div>
      <div class="stats"><span><b>${counts[p.slug] || 0}</b> need you</span><span><b>${p.open_tasks ?? '—'}</b> open</span><span><b>${p.live_sessions ?? 0}</b> live</span><span>${p.last_activity ? ago(p.last_activity) + ' ago' : ''}</span></div>
      <div class="roots">${(p.repo_roots || []).join(' · ') || 'No repo folders'}</div>
      <div class="acts"><${Btn} label=${p.slug === ctx.project() ? 'Open (current)' : 'Open'} kind="p" onClick=${() => { ctx.setProject(p.slug); ctx.go('command'); }} />
        <${Btn} label="Connect agents" onClick=${() => A.connect(p.slug)} /><${Btn} label="Settings" onClick=${() => A.projectSettings(p)} /></div>
    </div>`)}</div>
  </section>`;
}
