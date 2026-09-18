'use strict';

const $=s=>document.querySelector(s);
let board=null, submitting=false, dialogAction=null, notificationProject='', notificationCursor=0;

const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time=s=>new Date(s).toLocaleString('en-GB',{timeZone:'Asia/Karachi',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
const project=()=>$('#project').value.trim()||'media-intelligence';
const taskFor=id=>board?.tasks.find(t=>t.id===id);
const sessionFor=id=>board?.sessions.find(s=>s.id===id);
const shortId=id=>id?String(id).slice(-8):'';
const name=id=>id==='rohan'?'the owner':sessionFor(id)?.name||id||'Unassigned';
const sessionLabel=id=>{
 const s=sessionFor(id);
 return s?`${s.name} · ${s.branch} · ${shortId(s.id)}${s.imported?' · imported':''}`:name(id);
};
const recipientLabel=id=>({all:'Everyone',rohan:'the owner',codex:'All Codex sessions',claude:'All Claude sessions'}[id]||sessionLabel(id));
const activeSessions=()=>board.sessions.filter(s=>!s.imported);
const notificationKey=projectName=>`project-desk:last-seen:${projectName}`;
const significantEvent=e=>e&&(
 e.kind.startsWith('task.')||e.kind.startsWith('handoff.')||e.kind==='message.sent'||
 e.kind==='note.created'||e.kind==='changelog.published');

function maxEventSeq(){return Math.max(0,...(board?.events||[]).map(e=>Number(e.seq)||0));}
function notificationText(e){
 let data=e.data||{};
 if(typeof data==='string'){try{data=JSON.parse(data);}catch(err){data={};}}
 const task=taskFor(data.task_id), message=board.messages.find(m=>m.id===data.message_id);
 const subject=task?` · ${task.title}`:message?` · ${message.body.slice(0,90)}${message.body.length>90?'…':''}`:'';
 return `${name(e.actor)} · ${e.kind.replaceAll('.',' ')}${subject}`;
}
function syncNotificationCursor(){
 const current=project();
 if(notificationProject===current)return;
 notificationProject=current;
 let stored=null;
 try{stored=localStorage.getItem(notificationKey(current));}catch(e){}
 if(stored===null){
  notificationCursor=maxEventSeq();
  try{localStorage.setItem(notificationKey(current),String(notificationCursor));}catch(e){}
 }else notificationCursor=Math.max(0,Number(stored)||0);
}
function renderNotifications(){
 syncNotificationCursor();
 const events=(board.events||[]).filter(e=>significantEvent(e)&&Number(e.seq)>notificationCursor).sort((a,b)=>a.seq-b.seq);
 const oldest=Math.min(...(board.events||[]).map(e=>Number(e.seq)||Infinity));
 const truncated=events.length>0&&board.events.length>=100&&oldest>notificationCursor+1;
 const count=$('#notification-count');
 count.textContent=events.length>99?'99+':String(events.length);
 count.hidden=!events.length;
 $('#notification-limit').hidden=!truncated;
 $('#notification-list').innerHTML=events.length?events.map(e=>`<div class="notification-item" data-notification-seq="${esc(e.seq)}"><span>${time(e.created)}</span><p>${esc(notificationText(e))}</p></div>`).join(''):'<p class="notification-empty">No new updates.</p>';
}

function recipientOptions(value='all',include=''){
 const options=[
  {value:'all',label:'Everyone'},
  {value:'rohan',label:'the owner'},
  {value:'codex',label:'All Codex sessions'},
  {value:'claude',label:'All Claude sessions'}
 ];
 const ids=new Set(options.map(o=>o.value));
 [...activeSessions(),sessionFor(include)].filter(Boolean).forEach(s=>{
  if(!ids.has(s.id)){options.push({value:s.id,label:sessionLabel(s.id)});ids.add(s.id);}
 });
 return options;
}

async function refresh(){
 try{
  const r=await fetch('/api/state?project='+encodeURIComponent(project()));
  if(!r.ok)throw Error('Service unavailable');
  board=await r.json();
  renderNotifications();
  render();
  $('#connection').textContent='Updated '+time(board.generated_at);
 }catch(e){
  $('#connection').textContent='Disconnected';
  $('#notice').textContent='Cannot reach Project Desk. Displayed work may be out of date. Claims remain reserved.';
 }
}

function field(label,key,type='text',value='',options=[],isRequired=true){
 const required=isRequired?' required':'';
 if(type==='select')return `<label>${esc(label)}<select name="${esc(key)}"${required}>${options.map(o=>`<option value="${esc(o.value)}"${o.value===value?' selected':''}${o.disabled?' disabled':''}>${esc(o.label)}</option>`).join('')}</select></label>`;
 if(type==='textarea')return `<label>${esc(label)}<textarea name="${esc(key)}"${required}>${esc(value)}</textarea></label>`;
 return `<label>${esc(label)}<input name="${esc(key)}" value="${esc(value)}"${required}></label>`;
}

function openEditor(title,fieldsHtml,action){
 $('#editor-title').textContent=title;
 $('#editor-fields').innerHTML=fieldsHtml;
 $('#form-error').textContent='';
 dialogAction=action;
 $('#editor').showModal();
 $('#editor-fields input, #editor-fields textarea, #editor-fields select')?.focus();
}

async function mutate(action,data){
 const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Project-Desk':'dashboard'},body:JSON.stringify({project:project(),action,data})});
 const result=await r.json();
 if(!r.ok)throw Error(result.error||'Unable to save');
 return result;
}

function messageReceipt(m){
 const readers=m.acknowledgments||[];
 return readers.length
  ? `<div class="receipt read"><span class="receipt-state">Read</span> by ${readers.map(a=>esc(name(a.session_id))).join(', ')}</div>`
  : '<div class="receipt sent"><span class="receipt-state">Sent</span> · Awaiting read</div>';
}

function messageCard(m,compact=false){
 const task=taskFor(m.task_id);
 return `<article class="message${compact?' message-compact':''}" data-message="${esc(m.id)}">
  <div class="meta"><strong>${esc(name(m.sender))}</strong> <span class="message-direction">to ${esc(recipientLabel(m.recipient))}</span> · ${time(m.created)}</div>
  ${task?`<div class="message-task">Task: <strong>${esc(task.title)}</strong> <span>${esc(shortId(task.id))}</span></div>`:''}
  <p>${esc(m.body)}</p>
  ${messageReceipt(m)}
  <div class="message-actions"><button type="button" data-reply="${esc(m.id)}">Reply</button>${!compact&&['rohan','all'].includes(m.recipient)&&!(m.acknowledgments||[]).some(a=>a.session_id==='rohan')?`<button type="button" data-ack="${esc(m.id)}">Acknowledge</button>`:''}</div>
 </article>`;
}

function discussion(t){
 const comments=board.messages.filter(m=>m.task_id===t.id).sort((a,b)=>new Date(a.created)-new Date(b.created));
 return `<section class="discussion" aria-label="Discussion for ${esc(t.title)}">
  <div class="discussion-head"><h4>Discussion <span>${comments.length}</span></h4><button type="button" data-comment="${esc(t.id)}">Comment</button></div>
  <p class="discussion-limit">Recent dashboard history only; older comments may be omitted.</p>
  ${comments.length?comments.map(m=>messageCard(m,true)).join(''):'<p class="discussion-empty">No comments in the recent history. Add context for everyone working on this task.</p>'}
 </section>`;
}

function handoffBrief(t){
 const source=board.handoff_briefs;
 if(!source)return '';
 const brief=Array.isArray(source)?source.find(b=>b.task_id===t.id||b.id===t.id):source[t.id];
 if(!brief)return '';
 const sourceName=brief.source?.name||brief.source_session||'';
 const targetName=brief.target?.name||brief.target_session||'';
 const rows=[
  ['Progress',brief.progress],['Remaining work',brief.remaining_work],['Validation',brief.validation],
  ['Risks',brief.risks],['Commit',brief.commit_ref],['Branch',brief.branch],['Worktree',brief.worktree],
  ['Changed paths',Array.isArray(brief.changed_paths)?brief.changed_paths.join(' / '):brief.changed_paths]
 ].filter(([,value])=>value);
 return `<section class="handoff-brief"><div class="brief-label">Handoff brief · ${esc(brief.status||'offered')} <span>${esc(brief.created?time(brief.created):'')}</span></div>${sourceName||targetName?`<div class="brief-meta">${sourceName?`From ${esc(sourceName)}`:''}${targetName?` · To ${esc(targetName)}`:''}</div>`:''}${rows.map(([label,value])=>`<div class="brief-row"><strong>${esc(label)}</strong><span>${esc(value)}</span></div>`).join('')}${brief.accepted?`<div class="brief-meta">Accepted ${esc(time(brief.accepted))}${brief.accepted_by?` by ${esc(brief.accepted_by)}`:''}</div>`:''}</section>`;
}

function render(){
 const filter=$('#status-filter').value, q=$('#search').value.toLowerCase();
 const tasks=board.tasks.filter(t=>(filter==='all'||(filter==='active'?t.status!=='DONE':t.status===filter))&&JSON.stringify([t.title,name(t.owner),t.resources]).toLowerCase().includes(q));
 $('#count').textContent=tasks.length;
 $('#tasks').innerHTML=tasks.length?tasks.map(t=>`<article class="task" data-task="${esc(t.id)}">
  <div class="task-top"><div><h3>${esc(t.title)}</h3><div class="meta">${esc(name(t.owner)||t.assigned_to)} ${!t.owner&&t.assigned_to?'· Assigned to '+esc(t.assigned_to):''} · ${time(t.updated)}</div></div><div><span class="badge ${t.status.toLowerCase()}">${t.status}</span>${t.priority!=='normal'?`<span class="priority">${esc(t.priority)}</span>`:''}</div></div>
  ${t.imported?`<div class="imported">${t.status==='DONE'?'Imported completion report':'Imported report — ownership needs confirmation'}</div>`:''}
  ${t.pending_owner?`<div class="imported">Handoff offered to ${esc(name(t.pending_owner))}; awaiting acceptance</div>`:''}
  <p class="scope">${t.resources.map(esc).join(' / ')}</p><p class="next">${esc(t.status==='DONE'?t.summary:t.next_step)}</p>
  <details><summary>Evidence & work details</summary><pre>${esc('Task: '+t.id+'\nValidation: '+(t.validation||'Not recorded')+'\nCommit: '+(t.commit_ref||'Not recorded')+'\nDeployment: '+t.deployment+'\nWorktree: '+(board.sessions.find(s=>s.id===t.owner)?.worktree||'Not claimed')+'\nBranch: '+(board.sessions.find(s=>s.id===t.owner)?.branch||'Not claimed'))}</pre></details>
  ${handoffBrief(t)}
  ${discussion(t)}
  ${t.status!=='DONE'?`<div class="actions"><button data-action="${t.human_paused?'resume':'pause'}">${t.human_paused?'Resume':'Pause'}</button><button data-action="priority">Set priority</button><button data-action="reassign">Reassign</button><button data-action="close">Close with note</button></div>`:''}
 </article>`).join(''):'<p class="empty">No tasks in this view. Add a task or change the filter.</p>';
 $('#sessions').innerHTML=board.sessions.length?board.sessions.map(s=>`<div class="session"><span class="presence ${s.stale||s.imported?'stale':''}"></span><div><strong>${esc(s.name)}</strong> <span class="meta">${esc(s.kind)} · ${s.imported?'Imported, unconfirmed':s.stale?'Stale':'Checked in recently'}</span><div class="meta">${esc(s.branch)} · ${shortId(s.id)} · ${time(s.last_seen)}</div></div></div>`).join(''):'<p class="empty">Agents appear here after registering.</p>';
 $('#messages').innerHTML=board.messages.length?board.messages.map(m=>messageCard(m)).join(''):'<p class="empty">No messages yet. Send a request or leave a handoff for the team.</p>';
 $('#notes').innerHTML=board.notes.length?board.notes.map(n=>`<div class="note"><div class="meta">${esc(n.kind)} · ${esc(name(n.author))} · ${time(n.created)}</div><p>${esc(n.body)}</p></div>`).join(''):'<p class="empty">Record a decision so every session has the same context.</p>';
 $('#events').innerHTML=board.events.map(e=>`<div class="event">${time(e.created)} · ${esc(name(e.actor))} · ${esc(e.kind)}<details><summary>Details</summary>${esc(JSON.stringify(e.data))}</details></div>`).join('');
}

function composeMessage(taskId='',recipient='all',replyTo=''){
 const task=taskFor(taskId), original=board.messages.find(m=>m.id===replyTo);
 const context=task?`<p class="dialog-context">Task: <strong>${esc(task.title)}</strong> <span>${esc(shortId(task.id))}</span></p>`:'';
 const title=replyTo?'Reply in team inbox':task?'Comment on task':'Write to the team';
 openEditor(title,context+field('Recipient','recipient','select',recipient,recipientOptions(recipient,original?.sender))+field('Message','body','textarea'),d=>mutate('message',{recipient:d.recipient,body:d.body,...(taskId?{task_id:taskId}:{})}));
}

$('#editor-form').addEventListener('submit',async e=>{
 e.preventDefault();
 if(submitting)return;
 submitting=true;$('#save').disabled=true;
 try{const data=Object.fromEntries(new FormData(e.target));await dialogAction(data);$('#editor').close();$('#notice').textContent='Saved to the shared desk.';await refresh();}
 catch(e){$('#form-error').textContent=e.message;}
 finally{submitting=false;$('#save').disabled=false;}
});

$('#close-dialog').onclick=$('#cancel-dialog').onclick=()=>$('#editor').close();
$('#new-task').onclick=()=>openEditor('Add a task',field('Task title','title')+field('Assign to','assigned_to','select','',[{value:'',label:'Either agent'},{value:'codex',label:'Codex'},{value:'claude',label:'Claude'}],false)+field('Files or directories (one per line)','resources','textarea')+field('Expected outcome / next step','next_step','textarea'),d=>mutate('create',{...d,resources:d.resources.split('\n').map(s=>s.trim()).filter(Boolean)}));
$('#new-message').onclick=()=>composeMessage();
$('#new-note').onclick=()=>openEditor('Record your decision',field('Decision and reason','body','textarea'),d=>mutate('note',d));
$('#publish-update').onclick=()=>openEditor('Publish a progress update',
 field('Update title','title')+
 field('Task (optional)','task_id','select','',[{value:'',label:'Project-level update'},...board.tasks.map(t=>({value:t.id,label:`${t.title} · ${shortId(t.id)}`}))],false)+
 field('Update body','body','textarea')+
 field('Commit reference','commit_ref')+
 field('Validation evidence','validation','textarea'),
 d=>mutate('publish_update',{title:d.title,body:d.body,commit_ref:d.commit_ref,validation:d.validation,...(d.task_id?{task_id:d.task_id}:{})}));

$('#tasks').addEventListener('click',e=>{
 const comment=e.target.closest('[data-comment]');
 if(comment){composeMessage(comment.dataset.comment);return;}
 const reply=e.target.closest('[data-reply]');
 if(reply){const m=board.messages.find(x=>x.id===reply.dataset.reply);composeMessage(m?.task_id||'',m?.sender||'all',m?.id||'');return;}
 const button=e.target.closest('button[data-action]');
 if(!button)return;
 const t=taskFor(button.closest('[data-task]').dataset.task),action=button.dataset.action,base={task_id:t.id,version:t.version};
 if(action==='priority')openEditor('Set task priority',field('Priority','priority','select',t.priority,['normal','high','urgent'].map(v=>({value:v,label:v}))),d=>mutate(action,{...base,...d}));
 else if(action==='reassign'){
  const options=[{value:'',label:'Select a receiving session…',disabled:true},...activeSessions().map(s=>({value:s.id,label:sessionLabel(s.id)}))];
  openEditor('Reassign ownership',field('Receiving session','session_id','select','',options)+field('Reason / agreed handoff','reason','textarea'),d=>mutate(action,{...base,...d}));
 }
 else if(action==='close')openEditor('Close task and release its claims',field('Outcome or cancellation reason','summary','textarea'),d=>mutate(action,{...base,...d}));
 else openEditor(action==='pause'?'Pause this task':'Resume this task',`<p>${esc(t.title)}</p><p>${action==='pause'?'Claims stay reserved. The agent must check in to see your pause.':'This releases your pause instruction; it does not start an agent automatically.'}</p>`,()=>mutate(action,base));
});

$('#messages').addEventListener('click',async e=>{
 const reply=e.target.closest('[data-reply]');
 if(reply){const m=board.messages.find(x=>x.id===reply.dataset.reply);composeMessage(m?.task_id||'',m?.sender||'all',m?.id||'');return;}
 const b=e.target.closest('[data-ack]');
 if(!b)return;
 try{await mutate('ack',{message_id:b.dataset.ack});await refresh();}catch(e){$('#notice').textContent=e.message;}
});

$('#notifications').onclick=()=>{
 const panel=$('#notification-panel'),open=panel.hidden;
 panel.hidden=!open;
 $('#notifications').setAttribute('aria-expanded',String(open));
};
$('#mark-seen').onclick=()=>{
 notificationCursor=maxEventSeq();
 try{localStorage.setItem(notificationKey(project()),String(notificationCursor));}catch(e){}
 renderNotifications();
};

$('#refresh').onclick=refresh;
$('#project').addEventListener('change',refresh);
$('#status-filter').onchange=render;
$('#search').oninput=()=>board&&render();
refresh();
setInterval(()=>{if(!document.hidden&&!$('#editor').open)refresh();},3000);
