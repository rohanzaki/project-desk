// Every human action the page can take, as dialogs or direct calls. Action names and
// payloads are the desk's own (store.human); see design/redesign-2026-09-26/HANDOFF.md.
import {html} from '../vendor/preact-htm.module.js';
import * as api from './api.js';
import {HUMAN, whoFor} from './util.js';

const lines = text => String(text || '').split('\n').map(s => s.trim()).filter(Boolean);
const base = t => ({task_id: t.id, version: t.version});

export function sessionOptions(board, {includeStale = false} = {}) {
  return (board.sessions || []).filter(s => !s.imported && (includeStale || !s.stale))
    .sort((a, b) => String(b.last_seen).localeCompare(String(a.last_seen)))
    .map(s => ({value: s.id, label: `${s.name}${s.branch ? ' · ' + s.branch : ''}${s.stale ? ' (stale)' : ''}`}));
}

export function recipientOptions(board, include, includeLabel) {
  const options = [{value: 'all', label: 'Everyone'}, {value: 'codex', label: 'All Codex sessions'}, {value: 'claude', label: 'All Claude sessions'}];
  const seen = new Set(options.map(o => o.value));
  for (const o of sessionOptions(board)) if (!seen.has(o.value)) { options.push(o); seen.add(o.value); }
  for (const x of (board.crossovers || []).filter(x => x.status !== 'closed')) {
    if (!seen.has(x.id)) { options.push({value: x.id, label: `Crossover · ${x.title} (${x.members.map(m => m.project).join(' + ')})`}); seen.add(x.id); }
  }
  if (include && !seen.has(include)) options.unshift({value: include, label: includeLabel || include});
  return options;
}

export function makeActions(ctx) {
  const {project, act, dialog, flash} = ctx;
  const b = () => ctx.board;
  const A = {
    // ---- tasks
    pause: t => act(project(), 'pause', base(t), 'Paused. Claims stay reserved; the agent sees it on its next check-in.'),
    resume: t => act(project(), 'resume', base(t), 'Pause lifted. The agent resumes on its next check-in.'),
    reassign: t => dialog({title: 'Reassign ownership', save: 'Reassign',
      hint: 'The receiving session takes the claim now. The previous owner is told on its next check-in.',
      fields: [{key: 'session_id', label: 'Receiving session', type: 'select', options: sessionOptions(b(), {includeStale: false})},
               {key: 'reason', label: 'Reason / agreed handoff', type: 'area'}],
      onSubmit: f => act(project(), 'reassign', {...base(t), session_id: f.session_id, reason: f.reason}, `Reassigned to ${whoFor(b(), f.session_id).name}.`)}),
    priority: t => dialog({title: 'Set task priority', save: 'Save',
      fields: [{key: 'priority', label: 'Priority', type: 'select', value: t.priority || 'normal', options: ['normal', 'high', 'urgent'].map(v => ({value: v, label: v}))}],
      onSubmit: f => act(project(), 'priority', {...base(t), priority: f.priority}, `Priority set to ${f.priority}.`)}),
    close: t => dialog({title: 'Close task and release its claims', save: 'Close task',
      fields: [{key: 'summary', label: 'Outcome or cancellation reason', type: 'area'}],
      onSubmit: f => act(project(), 'close', {...base(t), summary: f.summary}, `Closed. ${t.resources.length} claim(s) released.`)}),
    reopen: t => dialog({title: 'Reopen & reassign completed task', save: 'Reopen',
      hint: 'Prior completion evidence stays attached. Reopening reacquires the recorded paths.',
      fields: [{key: 'session_id', label: 'Receiving session', type: 'select', options: sessionOptions(b())},
               {key: 'next_step', label: 'What should happen next', type: 'area'}],
      onSubmit: f => act(project(), 'reopen', {...base(t), session_id: f.session_id, next_step: f.next_step}, 'Reopened and paths re-claimed.')}),
    crossover: t => {
      const others = (ctx.projects || []).filter(p => p.slug !== project() && !p.archived);
      if (!others.length) { flash('No other project on this desk to cross over with.'); return; }
      dialog({title: 'Cross over into another project', save: 'Create link',
        hint: 'The other project joins with its own task in its own repo. You get a line to paste into its agent.',
        fields: [{key: 'invite', label: 'Other project', type: 'select', options: others.map(p => ({value: p.slug, label: `${p.name} (${p.slug})`}))},
                 {key: 'note', label: 'Note for them (optional)', type: 'area', optional: true, rows: 3}],
        onSubmit: async f => {
          const view = await api.mutate(project(), 'crossover.start', {task_id: t.id, invite: [f.invite], note: f.note || ''});
          ctx.refresh(true);
          setTimeout(() => A.crossoverLink(view), 0);
        }});
    },
    crossoverLink: view => dialog({title: `Crossover ${view.id}`, save: 'Done',
      fields: [{key: 'line', label: "Paste into the other project's Claude or Codex", type: 'readonly', value: view.paste_line || `Join Project Desk crossover ${view.id} from ${location.origin}/x/${view.id}`}],
      body: html`<p class="muted" style=${{fontSize: '12px', margin: 0}}><a href=${`/x/${encodeURIComponent(view.id)}`} target="_blank" rel="noopener">Open the join page</a>${view.awaiting_join && view.awaiting_join.length ? ` · waiting to join: ${view.awaiting_join.join(', ')}` : ''}</p>`}),
    comment: (t, body) => act(project(), 'message', {recipient: t.owner || 'all', body, task_id: t.id}, `Comment sent to ${whoFor(b(), t.owner).name}.`),
    newTask: () => dialog({title: 'Add a task', save: 'Add task',
      hint: 'It is queued for an agent to claim. Paths that overlap an open claim are refused, not merged.',
      fields: [{key: 'title', label: 'Task title', type: 'text'},
               {key: 'assigned_to', label: 'Assign to', type: 'select', options: [{value: '', label: 'Either agent'}, {value: 'codex', label: 'Codex'}, {value: 'claude', label: 'Claude'}]},
               {key: 'resources', label: 'Files or directories (one per line)', type: 'area', mono: true},
               {key: 'next_step', label: 'Expected outcome / next step', type: 'area'}],
      onSubmit: f => act(project(), 'create', {title: f.title, assigned_to: f.assigned_to, resources: lines(f.resources), next_step: f.next_step}, 'Task added to the queue.')}),

    // ---- messages
    message: (recipient = 'all', taskId = '', replyTo = '', include, includeLabel) => dialog({
      title: replyTo ? 'Reply' : taskId ? 'Comment on task' : 'Write to the team', save: 'Send',
      fields: [{key: 'recipient', label: 'Recipient', type: 'select', value: recipient, options: recipientOptions(b(), include || recipient, includeLabel)},
               {key: 'body', label: 'Message', type: 'area'}],
      onSubmit: f => act(project(), 'message', {recipient: f.recipient, body: f.body, ...(taskId ? {task_id: taskId} : {}), ...(replyTo ? {reply_to: replyTo} : {})}, 'Message sent.')}),
    ack: (proj, id) => act(proj, 'ack', {message_id: id}, 'Acknowledged.'),
    ackAll: async () => {
      try { const r = await api.mutate(project(), 'ack.inbox', {}); flash(`${r.acknowledged || 0} broadcast(s) acknowledged.`); ctx.refresh(true); }
      catch (error) {
        // An older desk without ack.inbox: one call per message.
        const ids = (b().messages || []).filter(m => m.recipient === 'all' && m.sender !== HUMAN && !(m.acknowledgments || []).some(a => a.session_id === HUMAN)).map(m => m.id);
        for (const id of ids) await api.mutate(project(), 'ack', {message_id: id});
        flash(`${ids.length} broadcast(s) acknowledged.`); ctx.refresh(true);
      }
    },

    // ---- needs-you answers (item.project may differ from the open board)
    decide: (item, option, note) => act(item.project, 'approval.decide', {approval_id: item.approval_id, decision: option, ...(note ? {note} : {})}, `Decision “${option}” sent.`),
    otherDecision: item => dialog({title: 'Another answer', save: 'Send decision',
      fields: [{key: 'decision', label: 'Your decision', type: 'text'}, {key: 'note', label: 'Note for the agent', type: 'area', optional: true}],
      onSubmit: f => A.decide(item, f.decision, f.note)}),
    answer: (item, body, alsoDecision) => act(item.project, 'answer', {message_id: item.message_id, body, also_decision: !!alsoDecision},
      alsoDecision ? 'Answered and saved as a project decision.' : 'Answer sent.'),
    answerDialog: item => dialog({title: 'Answer', save: 'Send answer',
      hint: firstLineOf(item.body || item.title),
      fields: [{key: 'body', label: 'Your answer', type: 'area'},
               {key: 'also', label: 'Also save it as a project decision, so every agent sees it', type: 'check'}],
      onSubmit: f => A.answer(item, f.body, f.also)}),
    refusal: (item, kind) => act(item.project, `refusal.${kind}`, {refusal_id: item.refusal_id},
      kind === 'queue' ? 'Queued: it gets YOUR TURN when the path frees.' : kind === 'handoff' ? 'Handoff request sent to the holder.' : 'Dismissed.'),
    snooze: (item, until) => act(item.project, 'snooze.set', {key: item.key, until: until.toISOString()}, `Snoozed until ${until.toLocaleString('en-GB', {weekday: 'short', hour: '2-digit', minute: '2-digit'})}.`),

    // ---- desk feature requests (agents ask, the human approves, a volunteer builds)
    approveRequest: (r, withNote) => {
      const item = {project: r.project || r.origin_project, request_id: r.request_id || r.id, title: r.title};
      if (!withNote) return act(item.project, 'desk_request.approve', {request_id: item.request_id}, 'Approved. Every project is told; the first willing agent takes it.');
      return dialog({title: 'Approve desk request', save: 'Approve', hint: item.title,
        fields: [{key: 'note', label: 'Note for whoever builds it (scope, limits)', type: 'area'}],
        onSubmit: f => act(item.project, 'desk_request.approve', {request_id: item.request_id, note: f.note}, 'Approved with your note.')});
    },
    rejectRequest: r => {
      const item = {project: r.project || r.origin_project, request_id: r.request_id || r.id, title: r.title};
      return dialog({title: 'Not approved', save: 'Send', hint: item.title,
        fields: [{key: 'note', label: 'Why (the agent that asked is told)', type: 'area', optional: true}],
        onSubmit: f => act(item.project, 'desk_request.reject', {request_id: item.request_id, note: f.note || ''}, 'Rejected; the asker is told.')});
    },

    // ---- action points
    tick: (proj, ids, status = 'done') => ids.length === 1
      ? act(proj, 'action.resolve', {item_id: ids[0], status}, status === 'open' ? 'Restored.' : 'Ticked.')
      : act(proj, 'action.clear', {item_ids: ids, status, note: status === 'done' ? 'Ticked from the dashboard' : 'Cleared from the dashboard'}, `${ids.length} action points ${status === 'done' ? 'ticked' : 'cleared'}.`),
    clearItems: (proj, ids, label) => act(proj, 'action.clear', {item_ids: ids}, `Cleared ${ids.length} action point(s)${label ? ' from ' + label : ''}. Restore them under “Recently done”.`),
    addActionItems: () => dialog({title: 'Add action points', save: 'Add',
      fields: [{key: 'body', label: 'One per line', type: 'area'},
               {key: 'assignee', label: 'For', type: 'select', options: [{value: 'human', label: 'Me (the human)'}, {value: 'agents', label: 'Any agent in this project'}]}],
      onSubmit: f => act(project(), 'action.add', {body: f.body, assignee: f.assignee}, 'Added.')}),

    // ---- knowledge
    decision: () => dialog({title: 'Record your decision', save: 'Record',
      hint: 'Every agent in this project gets it in its next hook check-in (ROHAN DECISION).',
      fields: [{key: 'body', label: 'Decision and reason', type: 'area'}],
      onSubmit: f => act(project(), 'note', {body: f.body}, 'Decision recorded.')}),
    publishUpdate: () => dialog({title: 'Publish a verified update', save: 'Publish',
      fields: [{key: 'title', label: 'Title', type: 'text'}, {key: 'body', label: 'What changed and how to use it', type: 'area'},
               {key: 'commit_ref', label: 'Commit', type: 'text', mono: true}, {key: 'validation', label: 'Validation evidence', type: 'area'},
               {key: 'task_id', label: 'Task id (optional)', type: 'text', mono: true, optional: true}],
      onSubmit: f => act(project(), 'publish_update', {title: f.title, body: f.body, commit_ref: f.commit_ref, validation: f.validation, ...(f.task_id ? {task_id: f.task_id} : {})}, 'Update published.')}),
    lesson: () => dialog({title: 'Add a lesson for every agent', save: 'Save lesson',
      fields: [{key: 'body', label: 'Lesson (a few sentences, with the why)', type: 'area'},
               {key: 'paths', label: 'Paths it applies to (one per line)', type: 'area', mono: true, optional: true, rows: 3},
               {key: 'tags', label: 'Tags (optional)', type: 'text', optional: true},
               {key: 'scope', label: 'Scope', type: 'select', options: [{value: 'project', label: 'This project'}, {value: 'global', label: 'Every project'}]}],
      onSubmit: f => act(project(), 'lesson.create', {body: f.body, paths: f.paths, tags: f.tags, scope: f.scope}, 'Lesson saved. Agents get it when they claim those paths.')}),
    archiveLesson: l => dialog({title: 'Archive lesson', save: 'Archive',
      fields: [{key: 'reason', label: 'Why it no longer applies', type: 'area'}],
      onSubmit: f => act(project(), 'lesson.archive', {lesson_id: l.id, reason: f.reason}, 'Lesson archived.')}),

    // ---- projects & desk
    connect: async (slug = project()) => {
      try {
        const info = await api.connect(slug);
        dialog({title: `Connect agents · ${info.name}`, save: 'Done',
          fields: [{key: 'line', label: 'Paste into Claude or Codex, in this repo', type: 'readonly', value: info.paste_line},
                   {key: 'cmd', label: 'Or run in the repo folder', type: 'readonly', value: info.join_command}],
          body: html`<p class="muted" style=${{fontSize: '12px', margin: 0}}><a href=${info.kit_url}>Download the setup kit (zip)</a> · <a href=${info.onboard_url} target="_blank" rel="noopener">Open the agent instructions</a></p>`});
      } catch (error) { flash(error.message, true); }
    },
    newProject: () => dialog({title: 'New project', save: 'Create project',
      fields: [{key: 'name', label: 'Name', type: 'text'}, {key: 'slug', label: 'Short name (lowercase, dashes)', type: 'text', optional: true},
               {key: 'repo_roots', label: 'Repo folders (one absolute path per line)', type: 'area', mono: true}],
      onSubmit: async f => {
        const slug = (f.slug || f.name).trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
        await api.mutate(project(), 'project.create', {slug, name: f.name.trim(), repo_roots: lines(f.repo_roots)});
        await ctx.loadProjects(); ctx.setProject(slug); setTimeout(() => A.connect(slug), 0);
      }}),
    projectSettings: p => dialog({title: `Project settings · ${p.name}`, save: 'Save',
      fields: [{key: 'name', label: 'Name', type: 'text', value: p.name},
               {key: 'repo_roots', label: 'Repo folders (one absolute path per line)', type: 'area', mono: true, value: (p.repo_roots || []).join('\n'), optional: true},
               {key: 'rules_path', label: 'Rules file (optional absolute path)', type: 'text', mono: true, value: p.rules_path || '', optional: true},
               {key: 'archived', label: 'Archived (hidden from the switcher)', type: 'check', value: !!p.archived}],
      onSubmit: async f => {
        await api.mutate(p.slug, 'project.update', {slug: p.slug, name: f.name.trim(), repo_roots: lines(f.repo_roots), rules_path: (f.rules_path || '').trim(), archived: !!f.archived});
        await ctx.loadProjects(); flash('Project saved.');
      }}),
    announce: () => dialog({title: 'Announce a Project Desk restart', save: 'Announce',
      hint: "Posts the notice to every active project's board. The desk posts “Project Desk is back” by itself after it starts. VS Code sessions that are not connected to the desk do not see this: message them too (Bidder's included).",
      fields: [{key: 'seconds', label: 'Starts in (seconds)', type: 'text', value: '60'}, {key: 'reason', label: 'Reason (optional)', type: 'text', optional: true}],
      onSubmit: async f => { const r = await api.mutate(project(), 'desk.announce', f); flash('Restart announced to: ' + Object.keys(r.announced_to || {}).join(', ')); }}),
  };
  return A;
}

const firstLineOf = text => String(text || '').split('\n').find(Boolean) || '';
