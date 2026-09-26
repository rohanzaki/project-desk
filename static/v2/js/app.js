import {html, render, useState, useEffect, useRef, useMemo, useCallback} from '../vendor/preact-htm.module.js';
import {Desk} from './context.js';
import * as api from './api.js';
import {Toast, Dialog} from './ui.js';
import {makeActions} from './actions.js';
import {Palette, VIEWS} from './palette.js';
import {NeedsYou, SinceStrip, OnDesk, itemActions} from './views/command.js';
import {Ledger} from './views/ledger.js';
import {TaskPane} from './views/pane.js';
import {Inbox, ActionPoints, Notes, Lessons, Activity} from './views/lists.js';
import {Claims, Lanes, Projects} from './views/ops.js';
import {DeskRequests} from './views/requests.js';
import {HUMAN, store, LAST_PROJECT_KEY, pinKey, lookedKey, notifyKey, indexBoard, taskOf, whoFor, firstLine, when, ago} from './util.js';

const BOARD_MS = 4000, ATTENTION_MS = 8000, PROJECTS_MS = 30000;
const PANE_VIEWS = new Set(['command', 'tasks']);

function initialProject() {
  const path = location.pathname.match(/\/p\/([a-z0-9][a-z0-9-]*)\/?$/);
  const query = new URLSearchParams(location.search).get('project');
  return (path && path[1]) || query || store.raw(LAST_PROJECT_KEY) || 'media-intelligence';
}
const initialView = () => { const v = location.hash.replace(/^#\/?/, '').split('/')[0]; return VIEWS.some(([k]) => k === v) ? v : 'command'; };

// "Needs you" for this project only, when the desk has no /api/attention yet.
function localAttention(b, project) {
  if (!b) return [];
  const items = [], t = id => b._taskMap && b._taskMap.get(id);
  for (const a of b.approvals || []) if (a.status === 'pending') items.push({key: `approval:${a.id}`, project, kind: 'approval', approval_id: a.id, options: a.options || [], title: a.title, task_id: a.task_id, task_title: (t(a.task_id) || {}).title, who: {id: a.requester}, created: a.created});
  for (const q of b.open_questions || []) {
    const m = (b.messages || []).find(x => x.id === q.message_id);
    if (['rohan', 'human'].includes(q.recipient)) items.push({key: `question:${q.message_id}`, project, kind: 'question', message_id: q.message_id, title: m ? m.body : 'Open question', body: m ? m.body : '', task_id: m && m.task_id, who: {id: q.asker}, created: q.created});
  }
  for (const s of b.stale_claims || []) items.push({key: `stale:${s.task_id}`, project, kind: 'stale', task_id: s.task_id, title: `${s.title} — owner silent ${ago(s.owner_last_seen)}, still holds ${(s.resources || []).join(', ')}`, meta: 'Claims never expire on their own', who: {id: s.owner, name: s.owner_name}, created: s.owner_last_seen});
  const byTask = new Map();
  for (const i of b.action_items || []) if (i.status === 'open' && i.assignee === HUMAN) { const k = i.task_id || ''; if (!byTask.has(k)) byTask.set(k, []); byTask.get(k).push(i); }
  for (const [k, list] of byTask) items.push({key: `action:${project}:${k || 'none'}`, project, kind: 'action', item_ids: list.map(i => i.id), task_id: k || null, task_title: list[0].task_title,
    title: list.map(i => i.body).join(' · '), meta: `${list.length} left for you on ${list[0].task_title || 'no task'}`, who: {id: list[0].author, name: list[0].author_name}, created: list[0].created});
  const order = {approval: 0, question: 1, desk_request: 2, blocked: 3, stale: 4, refused: 5, action: 6};
  return items.sort((x, y) => order[x.kind] - order[y.kind] || String(y.created).localeCompare(String(x.created)));
}

function App() {
  const [project, setProjectState] = useState(initialProject);
  const [board, setBoard] = useState(null);
  const [boardVersion, setBoardVersion] = useState(0);
  const [att, setAtt] = useState({items: [], counts: {}});
  const [showSnoozed, setShowSnoozed] = useState(false);
  const [projects, setProjects] = useState([]);
  const [view, setView] = useState(initialView);
  const [sel, setSel] = useState(null);
  const [paneOpen, setPaneOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [palette, setPalette] = useState(false);
  const [dialog, setDialog] = useState(null);
  const [toast, setToast] = useState(null);
  const [live, setLive] = useState({ok: true, error: ''});
  const [width, setWidth] = useState(window.innerWidth);
  const [doneDays, setDoneDays] = useState(7);
  const [navOpen, setNavOpen] = useState(false);
  const [pins, setPins] = useState(() => new Set(store.get(pinKey(initialProject()), [])));
  const [digest, setDigest] = useState(null);
  const [lookedSince, setLookedSince] = useState(null);
  const [notify, setNotify] = useState(() => store.get(notifyKey, false) && typeof Notification !== 'undefined' && Notification.permission === 'granted');
  const [, force] = useState(0);
  const ui = useRef({}).current;
  const etag = useRef(null), seenKeys = useRef(null), toastTimer = useRef(null), latest = useRef({});

  const rerender = useCallback(() => force(n => n + 1), []);
  const flash = useCallback((text, error = false) => {
    clearTimeout(toastTimer.current); setToast({text, error});
    toastTimer.current = setTimeout(() => setToast(null), error ? 7000 : 4200);
  }, []);

  // ---- data -------------------------------------------------------------------
  const loadBoard = useCallback(async (forceFull = false) => {
    try {
      const r = await api.board(latest.current.project, forceFull ? null : etag.current, latest.current.doneDays);
      if (r.unchanged) { setLive({ok: true, error: ''}); return; }
      etag.current = r.etag;
      setBoard(indexBoard(r.data)); setBoardVersion(v => v + 1); setLive({ok: true, error: ''});
    } catch (error) { setLive({ok: false, error: error.message}); }
  }, []);
  const loadAttention = useCallback(async () => {
    try {
      const all = await api.attention('all', latest.current.showSnoozed ? 1 : 0);
      if (all) {
        const items = all.items || [];
        const snoozed = all.snoozed_total ?? items.filter(i => i.snoozed_until).length;
        setAtt({items, counts: all.counts || {}, snoozed, showingSnoozed: latest.current.showSnoozed});
      } else {
        const items = localAttention(latest.current.board, latest.current.project);
        setAtt({items, counts: {[latest.current.project]: items.length}, local: true});
      }
    } catch (error) { /* keep the last list; the live dot shows the outage */ }
  }, []);
  const loadProjects = useCallback(async () => { try { setProjects(await api.projects()); } catch (error) { /* ignore */ } }, []);
  const refresh = useCallback((full = false) => { loadBoard(full); loadAttention(); }, []);

  latest.current = {project, board, doneDays, showSnoozed, view, sel, cursor, palette, dialog, paneOpen, att};

  useEffect(() => { etag.current = null; setBoard(null); setSel(null); setPins(new Set(store.get(pinKey(project), []))); loadBoard(true); loadAttention(); loadDigest(); }, [project]);
  useEffect(() => { etag.current = null; loadBoard(true); }, [doneDays]);
  useEffect(() => { loadAttention(); }, [showSnoozed]);
  useEffect(() => { if (att.local) loadAttention(); }, [boardVersion]);
  useEffect(() => {
    loadProjects();
    const tick = (fn, ms) => setInterval(() => { if (!document.hidden) fn(); }, ms);
    const a = tick(() => loadBoard(), BOARD_MS), b = tick(loadAttention, ATTENTION_MS), c = tick(loadProjects, PROJECTS_MS);
    const onResize = () => setWidth(window.innerWidth);
    const onVis = () => { if (document.hidden) store.set(lookedKey(latest.current.project), new Date().toISOString()); else refresh(); };
    window.addEventListener('resize', onResize); document.addEventListener('visibilitychange', onVis);
    window.addEventListener('pagehide', () => store.set(lookedKey(latest.current.project), new Date().toISOString()));
    return () => { clearInterval(a); clearInterval(b); clearInterval(c); window.removeEventListener('resize', onResize); document.removeEventListener('visibilitychange', onVis); };
  }, []);

  // "Since you last looked": the moment this browser last left the page.
  async function loadDigest() {
    const since = store.get(lookedKey(project), null);
    setLookedSince(since); setDigest(null);
    if (!since || Date.now() - Date.parse(since) < 5 * 60e3) return;
    try { setDigest(await api.digest(project, since)); } catch (error) { setDigest(null); }
  }
  const markLooked = () => { store.set(lookedKey(project), new Date().toISOString()); setLookedSince(null); setDigest(null); };

  // Select the first task once a board arrives; keep the URL hash on the view.
  useEffect(() => { if (board && (!sel || !taskOf(board, sel))) { const first = (board.tasks || []).find(t => t.status !== 'DONE') || (board.tasks || [])[0]; if (first) setSel(first.id); } }, [boardVersion]);
  useEffect(() => { if (location.hash.replace('#', '') !== view) history.replaceState(null, '', `${location.pathname}${location.search}#${view}`); }, [view]);
  useEffect(() => { const n = att.items.length; document.title = n ? `(${n}) Project Desk` : 'Project Desk'; }, [att]);

  // Browser notifications for new "Needs you" items (opt-in).
  useEffect(() => {
    const keys = new Set(att.items.map(i => i.key));
    if (seenKeys.current && notify && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
      for (const i of att.items) if (!seenKeys.current.has(i.key)) {
        try { new Notification(`Project Desk · ${i.kind}${i.project !== project ? ' · ' + i.project : ''}`, {body: firstLine(i.title, 180), tag: i.key}); } catch (error) { /* ignore */ }
      }
    }
    seenKeys.current = keys;
  }, [att]);
  const toggleNotify = async () => {
    if (notify) { setNotify(false); store.set(notifyKey, false); return; }
    if (typeof Notification === 'undefined') { flash('This browser has no notifications.', true); return; }
    const perm = Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission();
    if (perm === 'granted') { setNotify(true); store.set(notifyKey, true); flash('You will get a notification when something new needs you.'); }
    else flash('Notifications are blocked for this page in the browser settings.', true);
  };

  // ---- actions --------------------------------------------------------------------
  const act = useCallback(async (proj, action, data, msg) => {
    try { await api.mutate(proj, action, data); if (msg) flash(msg); }
    catch (error) { flash(error.message, true); throw error; }
    finally { refresh(true); }
  }, []);
  const setProject = slug => { if (!slug || slug === project) return; store.setRaw(LAST_PROJECT_KEY, slug); setProjectState(slug);
    const url = location.pathname.startsWith('/v2') ? `/v2?project=${encodeURIComponent(slug)}` : `/p/${slug}`;
    history.replaceState(null, '', `${url}#${view}`); setNavOpen(false); };
  const go = v => { setView(v); setPalette(false); setNavOpen(false); if (!PANE_VIEWS.has(v)) setPaneOpen(false); };
  const select = (id, opts = {}) => { setSel(id); if (opts.view) setView(opts.view); if (opts.open) setPaneOpen(true); };
  const openTask = (id, proj) => { if (proj && proj !== project) { ui.pendingSel = id; setProject(proj); } select(id, {open: true, view: PANE_VIEWS.has(view) ? view : 'tasks'}); };
  useEffect(() => { if (ui.pendingSel && board && taskOf(board, ui.pendingSel)) { setSel(ui.pendingSel); setPaneOpen(true); ui.pendingSel = null; } }, [boardVersion]);
  const togglePin = id => setPins(prev => { const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); store.set(pinKey(project), [...next]); return next; });

  const wide = width >= 1280, phone = width < 760;
  const paneView = PANE_VIEWS.has(view);
  const paneColumn = paneView && wide;
  const navW = phone ? 0 : width < 900 ? 168 : 216;
  const mainWidth = width - navW - (paneColumn ? 404 : 0) - 48;

  const ctx = useMemo(() => ({}), []);
  Object.assign(ctx, {
    project: () => latest.current.project, board: board || {}, boardVersion, attention: att, projects, view, sel, cursor, ui, pins, doneDays, mainWidth, digest, lookedSince,
    act, dialog: spec => setDialog(spec), flash, refresh, rerender, go, select, openTask, setCursor, togglePin, setProject, loadProjects, markLooked,
    setDoneDays, closePane: () => setPaneOpen(false), toggleSnoozed: () => setShowSnoozed(s => !s),
  });
  ctx.actions = useMemo(() => makeActions(ctx), []);

  // ---- keyboard ---------------------------------------------------------------------
  useEffect(() => {
    const onKey = e => {
      const s = latest.current, k = e.key;
      if ((e.metaKey || e.ctrlKey) && k.toLowerCase() === 'k') { e.preventDefault(); setPalette(p => !p); return; }
      if (s.dialog || s.palette) return;   // they handle their own keys
      if (k === 'Escape') { if (s.paneOpen) setPaneOpen(false); setNavOpen(false); return; }
      const tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.metaKey || e.ctrlKey || e.altKey) return;
      if (k === '/') { e.preventDefault(); setPalette(true); return; }
      const items = ui.att || [];
      if (s.view === 'command' && items.length) {
        const cur = items[Math.min(s.cursor, items.length - 1)];
        if (k === 'j') { setCursor(c => Math.min(items.length - 1, c + 1)); return; }
        if (k === 'k') { setCursor(c => Math.max(0, c - 1)); return; }
        if (/^[1-9]$/.test(k)) { const b = itemActions(ctx, cur)[Number(k) - 1]; if (b) { e.preventDefault(); b.run(); } return; }
        if (k === 'e') { const b = itemActions(ctx, cur).find(x => x.key === 'E'); if (b) b.run(); return; }
        if (k === 's') { ui.snoozeFor = cur.key; rerender(); return; }
        if (k === 'Enter' && cur.task_id) { openTask(cur.task_id, cur.project); return; }
      }
      if (PANE_VIEWS.has(s.view) && (k === 'ArrowDown' || k === 'ArrowUp')) {
        const list = ui.visible || []; if (!list.length) return;
        e.preventDefault();
        const i = list.findIndex(t => t.id === s.sel);
        const next = list[k === 'ArrowDown' ? Math.min(list.length - 1, i + 1) : Math.max(0, i - 1)];
        if (next) setSel(next.id);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // ---- render ---------------------------------------------------------------------
  if (!board) {
    return html`<div class="shell"><${Top} ctx=${ctx} live=${live} projects=${projects} project=${project} setProject=${setProject} openPalette=${() => setPalette(true)} phone=${phone} onMenu=${() => setNavOpen(!navOpen)} />
      <div class="empty">${live.ok ? 'Loading the desk…' : `Cannot reach Project Desk: ${live.error}. Claims stay reserved; retrying.`}</div></div>`;
  }
  const b = board;
  const openN = (b.tasks || []).filter(t => t.status !== 'DONE').length;
  const unread = (b.messages || []).filter(m => ['rohan', 'all', 'human'].includes(m.recipient) && m.sender !== HUMAN && !(m.acknowledgments || []).some(a => a.session_id === HUMAN)).length;
  const aiN = (b.action_items || []).filter(i => i.status === 'open' && i.assignee === HUMAN).length;
  const heldN = (b.tasks || []).filter(t => t.status !== 'DONE').reduce((n, t) => n + t.resources.length, 0);
  const liveN = (b.sessions || []).filter(s => !s.stale && !s.imported).length;
  const counts = {command: att.items.length, tasks: openN, actions: aiN || '', inbox: unread || '', claims: heldN, lanes: liveN, lessons: (b.lessons || []).length, projects: projects.filter(p => !p.archived).length, requests: att.items.filter(i => i.kind === 'desk_request').length || ''};
  const views = {command: () => html`<${SinceStrip} /><${NeedsYou} /><${OnDesk} /><${Ledger} mode="command" />`, tasks: () => html`<${Ledger} mode="tasks" />`,
    actions: () => html`<${ActionPoints} />`, inbox: () => html`<${Inbox} />`, notes: () => html`<${Notes} />`, claims: () => html`<${Claims} />`,
    lanes: () => html`<${Lanes} />`, lessons: () => html`<${Lessons} />`, requests: () => html`<${DeskRequests} />`, activity: () => html`<${Activity} />`, projects: () => html`<${Projects} />`};
  const showPane = paneView && (paneColumn || paneOpen);
  return html`<${Desk.Provider} value=${ctx}>
    <div class="shell">
      <${Top} ctx=${ctx} live=${live} projects=${projects} project=${project} setProject=${setProject} openPalette=${() => setPalette(true)} phone=${phone}
        onMenu=${() => setNavOpen(!navOpen)} liveN=${liveN} attN=${att.items.length} go=${go} board=${b} />
      <div class=${`body ${paneColumn ? 'with-pane' : 'no-pane'}`}>
        <${Nav} ctx=${ctx} view=${view} go=${go} counts=${counts} projects=${projects} project=${project} setProject=${setProject} open=${navOpen} notify=${notify} toggleNotify=${toggleNotify} />
        <main class="main" id="main">
          ${b.legacy ? html`<div class="banner">This desk is running without the v2 endpoints yet: showing this project only, from /api/state.</div>` : ''}
          <div class="main-in">${(views[view] || views.command)()}</div>
        </main>
        ${showPane ? html`<${TaskPane} drawer=${!paneColumn} />` : ''}
      </div>
      ${phone ? html`<${BottomNav} view=${view} go=${go} counts=${counts} onMore=${() => setNavOpen(!navOpen)} />` : ''}
    </div>
    ${palette ? html`<${Palette} onClose=${() => setPalette(false)} />` : ''}
    ${dialog ? html`<${Dialog} spec=${dialog} onClose=${() => setDialog(null)} />` : ''}
    <${Toast} toast=${toast} />
  <//>`;
}

function Top({ctx, live, projects, project, setProject, openPalette, phone, onMenu, liveN = 0, attN = 0, go, board}) {
  const [menu, setMenu] = useState(false);
  const prod = board && (board.prod || []).slice().sort((x, y) => String(y.created).localeCompare(String(x.created)))[0];
  const laneHeld = board && (board.tasks || []).some(t => t.status !== 'DONE' && t.resources.some(r => r.startsWith('service:')));
  const cur = projects.find(p => p.slug === project);
  return html`<header class="top">
    <div class="brand">${phone ? html`<button type="button" class="top-btn" aria-label="Menu" onClick=${onMenu}>☰</button>` : ''}<span class="brand-mark">PD</span><span class="brand-name">Project Desk<span>.</span></span></div>
    <div class="top-mid">
      <div style=${{position: 'relative'}}>
        <button type="button" class="top-btn" onClick=${() => setMenu(!menu)} aria-haspopup="menu"><span class=${`dot${cur && !cur.live_sessions ? ' off' : ''}`}></span><span class="pname">${project}</span><span class="caret">▾</span></button>
        ${menu ? html`<div class="snooze-menu" style=${{left: 0, right: 'auto', top: '36px', minWidth: '240px'}} role="menu">
          ${projects.filter(p => !p.archived).map(p => html`<button type="button" role="menuitem" onClick=${() => { setMenu(false); setProject(p.slug); }}>${p.slug === project ? '● ' : ''}${p.name}${ctx.attention.counts && ctx.attention.counts[p.slug] ? ` · ${ctx.attention.counts[p.slug]} need you` : ''}</button>`)}
          <button type="button" role="menuitem" onClick=${() => { setMenu(false); go && go('projects'); }}>All projects…</button></div>` : ''}
      </div>
      <button type="button" class="search-btn" onClick=${openPalette}><span>Jump to a task, path, agent, message or command</span><span class="kbd">⌘K</span></button>
      ${prod ? html`<button type="button" class="prod-chip" onClick=${() => go && go('claims')} title="Latest deploy recorded on the desk"><span class="k">prod</span>${prod.service.replace('service:', '')} · ${String(prod.commit_ref || '').slice(0, 7)}<span class="k">·</span><span style=${{color: laneHeld ? '#e8bb78' : '#56ba79'}}>${laneHeld ? 'lane held' : 'lanes free'}</span></button>` : ''}
    </div>
    <div class="top-right">
      <span class="live"><span class=${`dot${live.ok ? '' : ' bad'}`}></span><span class="lbl">${live.ok ? `Live · ${liveN} agents` : 'Reconnecting…'}</span></span>
      <button type="button" class="bell" title="Needs you" onClick=${() => go && go('command')}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8"></path></svg>${attN ? html`<span class="count">${attN > 99 ? '99+' : attN}</span>` : ''}</button>
      <span class="avatar" title="Rohan">RZ</span>
    </div>
  </header>`;
}

const NAV = [['command', 'Command center'], ['tasks', 'Tasks'], ['actions', 'Action points'], ['inbox', 'Inbox'], ['notes', 'Decisions & notes'],
  ['claims', 'Claims & locks'], ['lanes', 'Agent lanes'], ['lessons', 'Lessons'], ['activity', 'Activity'], ['requests', 'Desk requests'], ['projects', 'Projects']];

function Nav({ctx, view, go, counts, projects, project, setProject, open, notify, toggleNotify}) {
  const b = ctx.board;
  const xos = (b.crossovers || []).filter(x => x.status !== 'closed');
  return html`<nav class=${`nav${open ? ' open' : ''}`} aria-label="Views">
    <div class="nav-group">${NAV.map(([v, l]) => html`<button type="button" class=${`nav-item${view === v ? ' on' : ''}`} onClick=${() => go(v)} aria-current=${view === v ? 'page' : undefined}>
      <span class="lbl">${l}</span><span class="n">${counts[v] ?? ''}</span></button>`)}</div>
    <div class="nav-group"><div class="nav-h">Projects</div>
      ${projects.filter(p => !p.archived).map(p => { const n = ctx.attention.counts && ctx.attention.counts[p.slug]; return html`<button type="button" class=${`proj${p.slug === project ? ' on' : ''}`} onClick=${() => setProject(p.slug)}>
        <span class=${`dot${p.live_sessions ? '' : ' off'}`}></span>${p.slug}${n ? html`<span class="att" title="Needs you">${n}</span>` : html`<span class="n">${p.open_tasks ?? ''}</span>`}</button>`; })}</div>
    ${xos.length ? html`<div class="nav-group" style=${{gap: '6px'}}><div class="nav-h" style=${{paddingBottom: 0}}>Crossovers</div>
      ${xos.map(x => { const mine = x.members.find(m => m.project === project && m.task_id); const waiting = x.members.filter(m => !m.signed_off).map(m => m.project);
        return html`<button type="button" class="xo-card" onClick=${() => mine ? ctx.select(mine.task_id, {open: true, view: 'tasks'}) : ctx.actions.crossoverLink(x)}>
          <span class="row"><strong>${x.title.length > 40 ? x.title.slice(0, 39) + '…' : x.title}</strong><span class="id">${x.id.slice(0, 8)}</span></span>
          <span class="sub">with ${x.members.map(m => m.project).filter(p => p !== project).join(', ')}${x.contract_version ? ` · contract v${x.contract_version}` : ''}</span>
          <span class=${`state${waiting.length ? '' : ' ok'}`}>${waiting.length ? `Awaiting sign-off: ${waiting.join(', ')}` : 'All signed off'}</span></button>`; })}</div>` : ''}
    <div class="nav-foot">
      <button type="button" class="btn-ghost" onClick=${() => ctx.actions.connect()}>Connect agents</button>
      <button type="button" class="btn-ghost" onClick=${toggleNotify}>${notify ? 'Notifications on' : 'Notify me'}</button>
      <div class="hint">⌘K palette · J/K move · 1–3 answer · E tick · S snooze · / search · <a href="/classic">classic page</a></div>
    </div>
  </nav>`;
}

function BottomNav({view, go, counts, onMore}) {
  const items = [['command', 'Needs you'], ['tasks', 'Tasks'], ['inbox', 'Inbox'], ['actions', 'Actions']];
  return html`<nav class="bnav" aria-label="Views">${items.map(([v, l]) => html`<button type="button" class=${view === v ? 'on' : ''} onClick=${() => go(v)}><span class="n">${counts[v] || ''}</span>${l}</button>`)}
    <button type="button" onClick=${onMore}><span class="n">☰</span>More</button></nav>`;
}

render(html`<${App} />`, document.getElementById('app'));
