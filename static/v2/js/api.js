// Talks to the desk that serves this page (same origin). Read endpoints are the v2
// ones from design/redesign-2026-09-26/SPEC.md; /api/state is the fallback while a
// desk without them is still running, so the page never goes blank.

const q = params => Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');

async function get(path, params = {}, headers = {}) {
  const response = await fetch(`${path}?${q(params)}`, {headers});
  if (response.status === 304) return {status: 304};
  let data = null;
  try { data = await response.json(); } catch (error) { data = null; }
  if (!response.ok) {
    const error = new Error((data && data.error) || `${path} answered ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return {status: response.status, data, etag: response.headers.get('etag')};
}

const missing = error => error && (error.status === 404 || error.status === 405);

export async function board(project, etag, doneDays) {
  try {
    const result = await get('/api/board', {project, done_days: doneDays}, etag ? {'If-None-Match': etag} : {});
    return result.status === 304 ? {unchanged: true} : {data: result.data, etag: result.etag || result.data.version};
  } catch (error) {
    if (!missing(error)) throw error;
    const result = await get('/api/state', {project});
    return {data: {...result.data, legacy: true}, etag: null};
  }
}

const optional = async (path, params) => {
  try { return (await get(path, params)).data; } catch (error) { if (missing(error)) return null; throw error; }
};

export const attention = (projects = 'all', includeSnoozed = 0) =>
  optional('/api/attention', {projects, include_snoozed: includeSnoozed});
export const taskDetail = (project, id) => optional('/api/task', {project, id});
export const digest = (project, since) => optional('/api/digest', {project, since});
export const search = (project, text) => optional('/api/search', {project, q: text});
export const events = (project, before, limit = 50) => optional('/api/events', {project, before, limit});
export const lanes = (project, hours = 12) => optional('/api/lanes', {project, hours});
export const projects = async () => (await get('/api/projects')).data.projects || [];
export const connect = async slug => (await get(`/api/projects/${encodeURIComponent(slug)}/connect`)).data;

export async function mutate(project, action, data = {}) {
  const response = await fetch('/api/action', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Project-Desk': 'dashboard'},
    body: JSON.stringify({project, action, data}),
  });
  let result = {};
  try { result = await response.json(); } catch (error) { result = {}; }
  if (!response.ok) throw new Error(result.error || `The desk refused ${action} (${response.status})`);
  return result;
}
