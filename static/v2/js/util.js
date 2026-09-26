import {html} from '../vendor/preact-htm.module.js';

export const HUMAN = 'rohan';
export const TZ = 'Asia/Karachi';

export const store = {
  get(key, fallback = null) { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private window */ } },
  raw(key) { try { return localStorage.getItem(key); } catch (e) { return null; } },
  setRaw(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* ignore */ } },
};
// Same keys as the classic page, so pins and the remembered project carry over.
export const LAST_PROJECT_KEY = 'project-desk:last-project';
export const pinKey = p => `project-desk:pinned-tasks:${p}`;
export const lookedKey = p => `project-desk:v2-looked:${p}`;
export const notifyKey = 'project-desk:v2-notify';

export const hm = iso => iso ? new Date(iso).toLocaleTimeString('en-GB', {timeZone: TZ, hour: '2-digit', minute: '2-digit'}) : '';
export const stamp = iso => iso ? new Date(iso).toLocaleString('en-GB', {timeZone: TZ, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}) : '';
export function ago(iso, now = Date.now()) {
  if (!iso) return '';
  const m = Math.round((now - new Date(iso).getTime()) / 60000);
  if (m < 1) return 'now';
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return m % 60 && h < 10 ? `${h}h ${m % 60}m` : `${h}h`;
  return `${Math.floor(h / 24)}d`;
}
export const when = iso => {
  if (!iso) return '';
  const d = new Date(iso), today = new Date();
  return d.toDateString() === today.toDateString() ? hm(iso) : stamp(iso);
};
export const firstLine = (text, n = 160) => {
  const line = String(text || '').split('\n').map(s => s.trim()).find(Boolean) || '';
  return line.length > n ? line.slice(0, n - 1) + '…' : line;
};
export const shortId = id => id ? String(id).slice(-6) : '';

// Same rule as deskcore.overlaps: services only match exactly; directories cover descendants.
export function overlaps(a, b) {
  a = String(a).replace(/\/+$/, ''); b = String(b).replace(/\/+$/, '');
  if (a.startsWith('service:') || b.startsWith('service:')) return a === b;
  return a === '.' || b === '.' || a === b || a.startsWith(b + '/') || b.startsWith(a + '/');
}

// ---- who is who -----------------------------------------------------------
export function whoFor(board, id, fallback = {}) {
  if (!id) return {id: '', kind: 'none', name: 'Unassigned', short: 'unassigned', ini: '··'};
  if (id === HUMAN || id === 'human') return {id: HUMAN, kind: 'human', name: 'Rohan (you)', short: 'you', ini: 'RZ'};
  if (id === 'project-desk') return {id, kind: 'none', name: 'Project Desk', short: 'desk', ini: 'PD'};
  if (id === 'all') return {id, kind: 'none', name: 'everyone', short: 'everyone', ini: 'AL'};
  if (id === 'codex' || id === 'claude') return {id, kind: id, name: `all ${id === 'codex' ? 'Codex' : 'Claude'}`, short: `all ${id}`, ini: id === 'codex' ? 'CX' : 'CL'};
  const s = (board && board._sessionMap && board._sessionMap.get(id)) || null;
  const kind = (s && s.kind) || fallback.kind || 'none';
  const name = (s && s.name) || fallback.name || id;
  return {id, kind, name, short: shortName(name), ini: kind === 'claude' ? 'CL' : kind === 'codex' ? 'CX' : '··', branch: s ? s.branch : '', stale: s ? s.stale : false, last_seen: s ? s.last_seen : ''};
}
export function shortName(name) {
  const n = String(name || '').replace(/\s*\(.*\)\s*$/, '');
  const tail = n.includes('·') ? n.split('·').pop().trim() : n;
  return tail.length > 26 ? tail.slice(0, 25) + '…' : tail;
}
export const Who = ({w, full}) => html`<span class=${`who ${w.kind}`} title=${w.name}><span class="ini">${w.ini}</span><span class="nm">${full ? w.name : w.short}</span></span>`;
export const Av = ({w, cls = ''}) => html`<span class=${`av ${w.kind} ${cls}`} title=${w.name}>${w.ini}</span>`;
export const Status = ({s}) => html`<span class=${`status st-${s}`}>${s}</span>`;

export function evidenceOf(board, taskId) {
  const e = board && board.evidence && board.evidence[taskId];
  if (!e) return {cls: 'none', text: '—'};
  return e.level === 'checked' ? {cls: 'checked', text: '✓ checked'} : {cls: 'failing', text: '✕ failing'};
}

// ---- indexes over a board ----------------------------------------------------
export function indexBoard(board) {
  if (!board) return board;
  board._sessionMap = new Map((board.sessions || []).map(s => [s.id, s]));
  board._taskMap = new Map((board.tasks || []).map(t => [t.id, t]));
  return board;
}
export const taskOf = (board, id) => board && board._taskMap ? board._taskMap.get(id) : null;

export const snoozeTimes = () => {
  const now = new Date();
  const tomorrow = new Date(now); tomorrow.setDate(now.getDate() + 1); tomorrow.setHours(9, 0, 0, 0);
  return [['1 hour', new Date(now.getTime() + 3600e3)], ['4 hours', new Date(now.getTime() + 4 * 3600e3)], ['Tomorrow 09:00', tomorrow]];
};
