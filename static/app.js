'use strict';

const $=selector=>document.querySelector(selector);
const $$=selector=>[...document.querySelectorAll(selector)];
let board=null,submitting=false,dialogAction=null,notificationProject='',notificationCursor=0;
let pinnedTasks=new Set();
const taskDetailState=new Map();
const ui={taskPage:1,messagePage:1,notePage:1,sessionPage:1,eventPage:1,rail:'inbox'};
const LIST_SIZES={messagePage:8,notePage:6,sessionPage:8,eventPage:12};

const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const time=value=>new Date(value).toLocaleString('en-GB',{timeZone:'Asia/Karachi',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
const project=()=>$('#project').value.trim()||'media-intelligence';
const taskFor=id=>board?.tasks.find(task=>task.id===id);
const sessionFor=id=>board?.sessions.find(session=>session.id===id);
const shortId=id=>id?String(id).slice(-8):'';
const name=id=>id==='rohan'?'the owner':sessionFor(id)?.name||id||'Unassigned';
const taskOwner=task=>task.owner||task.assigned_to||'';
const agentKind=id=>{
  if(id==='rohan')return 'rohan';
  if(id==='all')return 'all';
  if(id==='codex'||id==='claude')return id;
  return sessionFor(id)?.kind||'unknown';
};
const initials=id=>{
  const label=id==='all'?'All':name(id);
  return label.split(/\s+/).filter(Boolean).slice(0,2).map(part=>part[0]).join('').toUpperCase()||'?';
};
const agentTitle=id=>({rohan:'the owner',codex:'Codex',claude:'Claude',all:'Everyone',unknown:'Unknown'}[agentKind(id)]||name(id));
const agentChip=(id,extra='')=>`<span class="agent-chip agent-${esc(agentKind(id))} ${extra}"><span class="agent-avatar">${esc(initials(id))}</span><span>${esc(agentTitle(id))}</span></span>`;
const sessionLabel=id=>{
  const session=sessionFor(id);
  return session?`${session.name} · ${session.branch} · ${shortId(session.id)}${session.imported?' · imported':''}`:name(id);
};
const recipientLabel=id=>({all:'Everyone',rohan:'the owner',codex:'All Codex sessions',claude:'All Claude sessions'}[id]||sessionLabel(id));
const activeSessions=()=>board.sessions.filter(session=>!session.imported);
const notificationKey=projectName=>`project-desk:last-seen:${projectName}`;
const pinKey=projectName=>`project-desk:pinned-tasks:${projectName}`;
const significantEvent=event=>event&&(
  event.kind.startsWith('task.')||event.kind.startsWith('handoff.')||event.kind==='message.sent'||
  event.kind==='note.created'||event.kind==='changelog.published');

function maxEventSeq(){return Math.max(0,...(board?.events||[]).map(event=>Number(event.seq)||0));}

function notificationText(event){
  let data=event.data||{};
  if(typeof data==='string'){try{data=JSON.parse(data);}catch(error){data={};}}
  const task=taskFor(data.task_id),message=board.messages.find(item=>item.id===data.message_id);
  const subject=task?` · ${task.title}`:message?` · ${message.body.slice(0,90)}${message.body.length>90?'…':''}`:'';
  return `${name(event.actor)} · ${event.kind.replaceAll('.',' ')}${subject}`;
}

function notificationKind(event){
  if(event.kind.startsWith('handoff.'))return 'handoff';
  if(event.kind==='message.sent')return 'message';
  if(event.kind==='changelog.published')return 'changelog';
  if(event.kind==='note.created')return 'note';
  if(event.kind.startsWith('task.'))return 'task';
  return 'event';
}

function syncNotificationCursor(){
  const current=project();
  if(notificationProject===current)return;
  notificationProject=current;
  let stored=null;
  try{stored=localStorage.getItem(notificationKey(current));}catch(error){}
  if(stored===null){
    notificationCursor=maxEventSeq();
    try{localStorage.setItem(notificationKey(current),String(notificationCursor));}catch(error){}
  }else notificationCursor=Math.max(0,Number(stored)||0);
}

function syncPins(){
  try{
    const stored=JSON.parse(localStorage.getItem(pinKey(project()))||'[]');
    pinnedTasks=new Set(Array.isArray(stored)?stored:[]);
  }catch(error){pinnedTasks=new Set();}
}

function savePins(){
  try{localStorage.setItem(pinKey(project()),JSON.stringify([...pinnedTasks]));}catch(error){}
}

function renderNotifications(){
  syncNotificationCursor();
  const events=(board.events||[]).filter(event=>significantEvent(event)&&Number(event.seq)>notificationCursor).sort((a,b)=>a.seq-b.seq);
  const oldest=Math.min(...(board.events||[]).map(event=>Number(event.seq)||Infinity));
  const truncated=events.length>0&&board.events.length>=100&&oldest>notificationCursor+1;
  const count=$('#notification-count');
  count.textContent=events.length>99?'99+':String(events.length);
  count.hidden=!events.length;
  $('#notification-limit').hidden=!truncated;
  $('#notification-list').innerHTML=events.length?events.map(event=>`<div class="notification-item notification-${esc(notificationKind(event))}" data-notification-seq="${esc(event.seq)}"><div class="notification-item-top"><span class="event-type">${esc(notificationKind(event))}</span><span>${time(event.created)}</span></div><p>${esc(notificationText(event))}</p></div>`).join(''):'<p class="notification-empty">No new updates.</p>';
}

function recipientOptions(value='all',include=''){
  const options=[
    {value:'all',label:'Everyone'},
    {value:'rohan',label:'the owner'},
    {value:'codex',label:'All Codex sessions'},
    {value:'claude',label:'All Claude sessions'}
  ];
  const ids=new Set(options.map(option=>option.value));
  [...activeSessions(),sessionFor(include)].filter(Boolean).forEach(session=>{
    if(!ids.has(session.id)){options.push({value:session.id,label:sessionLabel(session.id)});ids.add(session.id);}
  });
  return options;
}

async function refresh(){
  try{
    const response=await fetch('/api/state?project='+encodeURIComponent(project()));
    if(!response.ok)throw Error('Service unavailable');
    board=await response.json();
    syncPins();
    renderNotifications();
    render();
    $('#connection').textContent='Updated '+time(board.generated_at);
    $('.connection-state').classList.remove('disconnected');
  }catch(error){
    $('#connection').textContent='Disconnected';
    $('.connection-state').classList.add('disconnected');
    $('#notice').textContent='Cannot reach Project Desk. Displayed work may be out of date. Claims remain reserved.';
  }
}

function field(label,key,type='text',value='',options=[],isRequired=true){
  const required=isRequired?' required':'';
  if(type==='select')return `<label>${esc(label)}<select name="${esc(key)}"${required}>${options.map(option=>`<option value="${esc(option.value)}"${option.value===value?' selected':''}${option.disabled?' disabled':''}>${esc(option.label)}</option>`).join('')}</select></label>`;
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
  const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-Project-Desk':'dashboard'},body:JSON.stringify({project:project(),action,data})});
  const result=await response.json();
  if(!response.ok)throw Error(result.error||'Unable to save');
  return result;
}

function paginate(items,key,size){
  const pages=Math.max(1,Math.ceil(items.length/size));
  ui[key]=Math.min(Math.max(1,ui[key]),pages);
  const start=(ui[key]-1)*size;
  return {items:items.slice(start,start+size),page:ui[key],pages,start,total:items.length};
}

function pageButtons(key,data){
  if(!data.total)return '';
  const first=data.start+1,last=Math.min(data.start+data.items.length,data.total);
  return `<span class="page-range">${first}–${last} of ${data.total}</span><div class="page-controls"><button type="button" data-page-list="${key}" data-page="${data.page-1}"${data.page===1?' disabled':''}>Previous</button><span>Page <strong>${data.page}</strong> / ${data.pages}</span><button type="button" data-page-list="${key}" data-page="${data.page+1}"${data.page===data.pages?' disabled':''}>Next</button></div>`;
}

function messageReceipt(message){
  const readers=message.acknowledgments||[];
  return readers.length
    ? `<div class="receipt read"><span class="receipt-copy"><span class="receipt-state">Read</span> by ${esc(readers.map(item=>name(item.session_id)).join(', '))}</span><span class="receipt-avatars">${readers.map(item=>agentChip(item.session_id,'agent-mini')).join(' ')}</span></div>`
    : '<div class="receipt sent"><span class="receipt-state">Sent</span> · Awaiting read</div>';
}

function messageCard(message,compact=false){
  const task=taskFor(message.task_id);
  return `<article class="message message-from-${esc(agentKind(message.sender))} message-to-${esc(agentKind(message.recipient))}${compact?' message-compact':''}" data-message="${esc(message.id)}">
    <div class="message-route"><div>${agentChip(message.sender)}<span class="route-arrow">to</span>${agentChip(message.recipient,'agent-recipient')}</div><time>${time(message.created)}</time></div>
    <div class="meta message-direction">${esc(name(message.sender))} to ${esc(recipientLabel(message.recipient))}</div>
    ${task?`<div class="message-task">Task: <strong>${esc(task.title)}</strong> <span>${esc(shortId(task.id))}</span></div>`:''}
    <p>${esc(message.body)}</p>
    ${messageReceipt(message)}
    <div class="message-actions"><button type="button" data-reply="${esc(message.id)}">Reply</button>${!compact&&['rohan','all'].includes(message.recipient)&&!(message.acknowledgments||[]).some(item=>item.session_id==='rohan')?`<button type="button" data-ack="${esc(message.id)}">Acknowledge</button>`:''}</div>
  </article>`;
}

function discussion(task){
  const comments=board.messages.filter(message=>message.task_id===task.id).sort((a,b)=>new Date(a.created)-new Date(b.created));
  return `<section class="discussion" aria-label="Discussion for ${esc(task.title)}">
    <div class="discussion-head"><h4>Discussion <span>${comments.length}</span></h4><button type="button" data-comment="${esc(task.id)}">Comment</button></div>
    <p class="discussion-limit">Recent dashboard history only; older comments may be omitted.</p>
    ${comments.length?comments.map(message=>messageCard(message,true)).join(''):'<p class="discussion-empty">No comments in recent history. Add context for everyone working on this task.</p>'}
  </section>`;
}

function handoffBrief(task){
  const source=board.handoff_briefs;
  if(!source)return '';
  const brief=Array.isArray(source)?source.find(item=>item.task_id===task.id||item.id===task.id):source[task.id];
  if(!brief)return '';
  const sourceName=brief.source?.name||brief.source_session||'',targetName=brief.target?.name||brief.target_session||'';
  const rows=[
    ['Progress',brief.progress],['Remaining work',brief.remaining_work],['Validation',brief.validation],
    ['Risks',brief.risks],['Commit',brief.commit_ref],['Branch',brief.branch],['Worktree',brief.worktree],
    ['Changed paths',Array.isArray(brief.changed_paths)?brief.changed_paths.join(' / '):brief.changed_paths]
  ].filter(([,value])=>value);
  return `<section class="handoff-brief"><div class="brief-label">Handoff brief · ${esc(brief.status||'offered')} <span>${esc(brief.created?time(brief.created):'')}</span></div>${sourceName||targetName?`<div class="brief-meta">${sourceName?`From ${esc(sourceName)}`:''}${targetName?` · To ${esc(targetName)}`:''}</div>`:''}${rows.map(([label,value])=>`<div class="brief-row"><strong>${esc(label)}</strong><span>${esc(value)}</span></div>`).join('')}${brief.accepted?`<div class="brief-meta">Accepted ${esc(time(brief.accepted))}${brief.accepted_by?` by ${esc(brief.accepted_by)}`:''}</div>`:''}</section>`;
}

function taskCard(task,expanded=false){
  const owner=taskOwner(task)||'all',isPinned=pinnedTasks.has(task.id);
  const comments=board.messages.filter(message=>message.task_id===task.id).length;
  return `<article class="task task-${esc(task.status.toLowerCase())}${isPinned?' is-pinned':''}" data-task="${esc(task.id)}">
    <div class="task-row task-top">
      <button type="button" class="pin-button" data-pin="${esc(task.id)}" aria-pressed="${isPinned}" aria-label="${isPinned?'Unpin':'Pin'} ${esc(task.title)}" title="${isPinned?'Unpin task':'Pin task to top'}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 3 8 0-1 6 3 3v2H6v-2l3-3-1-6Zm4 11v7"/></svg></button>
      <div class="task-main"><h3>${esc(task.title)}</h3><div class="task-owner">${agentChip(owner)}<strong class="owner-name">${esc(name(owner))}</strong><span class="meta">Updated ${time(task.updated)}</span></div></div>
      <div class="task-state"><span class="badge ${task.status.toLowerCase()}">${task.status}</span>${task.priority!=='normal'?`<span class="priority">${esc(task.priority)}</span>`:''}</div>
      <p class="task-next"><span>${task.status==='DONE'?'Outcome':'Next'}</span>${esc(task.status==='DONE'?(task.summary||'No completion summary recorded'):(task.next_step||'No next step recorded'))}</p>
    </div>
    <details class="task-body"${expanded?' open':''}>
      <summary><span>Task details</span><span>${task.resources.length} path${task.resources.length===1?'':'s'} · ${comments} comment${comments===1?'':'s'}</span></summary>
      <div class="task-detail-grid">
        <div><span class="detail-label">Scope</span><p class="scope">${task.resources.map(esc).join(' / ')||'No paths recorded'}</p></div>
        <div><span class="detail-label">Evidence</span><pre>${esc('Task: '+task.id+'\nValidation: '+(task.validation||'Not recorded')+'\nCommit: '+(task.commit_ref||'Not recorded')+'\nDeployment: '+task.deployment+'\nWorktree: '+(board.sessions.find(session=>session.id===task.owner)?.worktree||'Not claimed')+'\nBranch: '+(board.sessions.find(session=>session.id===task.owner)?.branch||'Not claimed'))}</pre></div>
      </div>
      ${task.imported?`<div class="imported">${task.status==='DONE'?'Imported completion report':'Imported report — ownership needs confirmation'}</div>`:''}
      ${task.pending_owner?`<div class="imported">Handoff offered to ${esc(name(task.pending_owner))}; awaiting acceptance</div>`:''}
      ${handoffBrief(task)}
      ${discussion(task)}
      ${task.status==='DONE'
        ? '<div class="actions"><button class="reopen-action" data-action="reopen">Reopen &amp; reassign</button></div>'
        : `<div class="actions"><button data-action="${task.human_paused?'resume':'pause'}">${task.human_paused?'Resume':'Pause'}</button><button data-action="priority">Set priority</button><button data-action="reassign">Reassign</button><button data-action="close">Close with note</button></div>`}
    </details>
  </article>`;
}

function updateOwnerFilter(){
  const select=$('#owner-filter'),current=select.value;
  const owners=[...new Set(board.tasks.map(task=>taskOwner(task)).filter(Boolean))].sort((a,b)=>name(a).localeCompare(name(b)));
  select.innerHTML='<option value="all">All owners</option><option value="unassigned">Unassigned</option>'+owners.map(owner=>`<option value="${esc(owner)}">${esc(name(owner))}</option>`).join('');
  select.value=[...select.options].some(option=>option.value===current)?current:'all';
}

function filteredTasks(){
  const status=$('#status-filter').value,owner=$('#owner-filter').value,priority=$('#priority-filter').value;
  const query=$('#search').value.trim().toLowerCase();
  const priorities={urgent:0,high:1,normal:2},statuses={BLOCKED:0,PAUSED:1,RUNNING:2,QUEUED:3,DONE:4};
  const tasks=board.tasks.filter(task=>{
    const ownerId=taskOwner(task);
    const statusMatch=status==='all'||(status==='active'?task.status!=='DONE':task.status===status);
    const ownerMatch=owner==='all'||(owner==='unassigned'?!ownerId:ownerId===owner);
    const priorityMatch=priority==='all'||task.priority===priority;
    const haystack=JSON.stringify([task.title,name(ownerId),task.resources,task.next_step,task.summary]).toLowerCase();
    return statusMatch&&ownerMatch&&priorityMatch&&(!query||haystack.includes(query));
  });
  const sort=$('#sort-tasks').value;
  return tasks.sort((a,b)=>{
    if(sort==='title')return a.title.localeCompare(b.title);
    if(sort==='priority')return (priorities[a.priority]??3)-(priorities[b.priority]??3)||new Date(b.updated)-new Date(a.updated);
    if(sort==='status')return (statuses[a.status]??5)-(statuses[b.status]??5)||new Date(b.updated)-new Date(a.updated);
    return new Date(b.updated)-new Date(a.updated);
  });
}

function renderTasks(){
  $$('#tasks .task').forEach(article=>{
    const details=article.querySelector('.task-body');
    if(details)taskDetailState.set(article.dataset.task,details.open);
  });
  const tasks=filteredTasks(),pinned=tasks.filter(task=>pinnedTasks.has(task.id)),regular=tasks.filter(task=>!pinnedTasks.has(task.id));
  const page=paginate(regular,'taskPage',Number($('#task-page-size').value)||10);
  const autoExpand=tasks.length<=3;
  $('#count').textContent=tasks.length;
  const expanded=task=>taskDetailState.has(task.id)?taskDetailState.get(task.id):autoExpand;
  const pinnedHtml=pinned.length?`<div class="task-group-label"><span>Pinned</span><strong>${pinned.length}</strong></div>${pinned.map(task=>taskCard(task,expanded(task))).join('')}`:'';
  const regularHtml=page.items.length?`<div class="task-group-label"><span>${pinned.length?'All other tasks':'Tasks'}</span><strong>${regular.length}</strong></div>${page.items.map(task=>taskCard(task,expanded(task))).join('')}`:'';
  $('#tasks').innerHTML=pinnedHtml+regularHtml||(tasks.length?'<p class="empty">Every matching task is pinned above.</p>':'<p class="empty">No tasks match this view. Clear a filter or add a task.</p>');
  $('#task-pagination').innerHTML=pageButtons('taskPage',page);
}

function renderMetrics(){
  const tasks=board.tasks;
  $('#metric-active').textContent=tasks.filter(task=>task.status!=='DONE').length;
  $('#metric-blocked').textContent=tasks.filter(task=>task.status==='BLOCKED').length;
  $('#metric-paused').textContent=tasks.filter(task=>task.status==='PAUSED').length;
  $('#metric-done').textContent=tasks.filter(task=>task.status==='DONE').length;
  $$('.overview-strip button').forEach(button=>button.classList.toggle('selected',button.dataset.quickStatus===$('#status-filter').value));
}

function renderRail(){
  $$('.rail-tabs [data-rail-tab]').forEach(button=>button.setAttribute('aria-selected',String(button.dataset.railTab===ui.rail)));
  $$('[data-rail-panel]').forEach(panel=>{panel.hidden=panel.dataset.railPanel!==ui.rail;panel.classList.toggle('active',panel.dataset.railPanel===ui.rail);});

  const messages=paginate(board.messages,'messagePage',LIST_SIZES.messagePage);
  $('#messages').innerHTML=messages.items.length?messages.items.map(message=>messageCard(message)).join(''):'<p class="empty">No messages yet. Send a request or leave a handoff for the team.</p>';
  $('#message-pagination').innerHTML=pageButtons('messagePage',messages);
  $('#message-tab-count').textContent=board.messages.length;

  const notes=paginate(board.notes,'notePage',LIST_SIZES.notePage);
  $('#notes').innerHTML=notes.items.length?notes.items.map(note=>`<article class="note"><div class="note-top"><span class="note-kind">${esc(note.kind)}</span><time>${time(note.created)}</time></div><div class="meta">${esc(name(note.author))}</div><p>${esc(note.body)}</p></article>`).join(''):'<p class="empty">Record a decision so every session has the same context.</p>';
  $('#note-pagination').innerHTML=pageButtons('notePage',notes);

  const orderedSessions=[...board.sessions].sort((a,b)=>Number(a.stale||a.imported)-Number(b.stale||b.imported)||new Date(b.last_seen)-new Date(a.last_seen));
  const sessions=paginate(orderedSessions,'sessionPage',LIST_SIZES.sessionPage);
  $('#sessions').innerHTML=sessions.items.length?sessions.items.map(session=>`<article class="session session-${esc(session.kind)}"><span class="presence ${session.stale||session.imported?'stale':''}"></span><div><div class="session-name"><strong>${esc(session.name)}</strong>${agentChip(session.id,'agent-mini')}</div><span class="meta">${esc(session.imported?'Imported, unconfirmed':session.stale?'Stale':'Checked in recently')}</span><div class="meta session-path">${esc(session.branch)} · ${shortId(session.id)} · ${time(session.last_seen)}</div></div></article>`).join(''):'<p class="empty">Agents appear here after registering.</p>';
  $('#session-pagination').innerHTML=pageButtons('sessionPage',sessions);
}

function renderEvents(){
  const ordered=[...board.events].sort((a,b)=>Number(b.seq)-Number(a.seq));
  const events=paginate(ordered,'eventPage',LIST_SIZES.eventPage);
  $('#events').innerHTML=events.items.map(event=>`<article class="event"><div><span class="event-type">${esc(event.kind.replaceAll('.',' '))}</span><strong>${esc(name(event.actor))}</strong></div><time>${time(event.created)}</time><details><summary>Details</summary><pre>${esc(JSON.stringify(event.data,null,2))}</pre></details></article>`).join('')||'<p class="empty">No activity recorded.</p>';
  $('#event-pagination').innerHTML=pageButtons('eventPage',events);
}

function render(){
  updateOwnerFilter();
  renderMetrics();
  renderTasks();
  renderRail();
  renderEvents();
}

function composeMessage(taskId='',recipient='all',replyTo=''){
  const task=taskFor(taskId),original=board.messages.find(message=>message.id===replyTo);
  const context=task?`<p class="dialog-context">Task: <strong>${esc(task.title)}</strong> <span>${esc(shortId(task.id))}</span></p>`:'';
  const title=replyTo?'Reply in team inbox':task?'Comment on task':'Write to the team';
  openEditor(title,context+field('Recipient','recipient','select',recipient,recipientOptions(recipient,original?.sender))+field('Message','body','textarea'),data=>mutate('message',{recipient:data.recipient,body:data.body,...(taskId?{task_id:taskId}:{})}));
}

$('#editor-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if(submitting)return;
  submitting=true;$('#save').disabled=true;
  try{
    const data=Object.fromEntries(new FormData(event.target));
    await dialogAction(data);
    $('#editor').close();
    $('#notice').textContent='Saved to the shared desk.';
    ui.messagePage=ui.notePage=1;
    await refresh();
  }catch(error){$('#form-error').textContent=error.message;}
  finally{submitting=false;$('#save').disabled=false;}
});

$('#close-dialog').onclick=$('#cancel-dialog').onclick=()=>$('#editor').close();
$('#new-task').onclick=()=>openEditor('Add a task',field('Task title','title')+field('Assign to','assigned_to','select','',[{value:'',label:'Either agent'},{value:'codex',label:'Codex'},{value:'claude',label:'Claude'}],false)+field('Files or directories (one per line)','resources','textarea')+field('Expected outcome / next step','next_step','textarea'),data=>mutate('create',{...data,resources:data.resources.split('\n').map(value=>value.trim()).filter(Boolean)}));
$('#new-message').onclick=()=>composeMessage();
$('#new-note').onclick=()=>openEditor('Record your decision',field('Decision and reason','body','textarea'),data=>mutate('note',data));
$('#publish-update').onclick=()=>openEditor('Publish a progress update',
  field('Update title','title')+
  field('Task (optional)','task_id','select','',[{value:'',label:'Project-level update'},...board.tasks.map(task=>({value:task.id,label:`${task.title} · ${shortId(task.id)}`}))],false)+
  field('Update body','body','textarea')+
  field('Commit reference','commit_ref')+
  field('Validation evidence','validation','textarea'),
  data=>mutate('publish_update',{title:data.title,body:data.body,commit_ref:data.commit_ref,validation:data.validation,...(data.task_id?{task_id:data.task_id}:{})}));

$('#tasks').addEventListener('click',event=>{
  const pin=event.target.closest('[data-pin]');
  if(pin){
    const id=pin.dataset.pin;
    pinnedTasks.has(id)?pinnedTasks.delete(id):pinnedTasks.add(id);
    savePins();
    renderTasks();
    return;
  }
  const comment=event.target.closest('[data-comment]');
  if(comment){composeMessage(comment.dataset.comment);return;}
  const reply=event.target.closest('[data-reply]');
  if(reply){const message=board.messages.find(item=>item.id===reply.dataset.reply);composeMessage(message?.task_id||'',message?.sender||'all',message?.id||'');return;}
  const button=event.target.closest('button[data-action]');
  if(!button)return;
  const task=taskFor(button.closest('[data-task]').dataset.task),action=button.dataset.action,base={task_id:task.id,version:task.version};
  if(action==='priority')openEditor('Set task priority',field('Priority','priority','select',task.priority,['normal','high','urgent'].map(value=>({value,label:value}))),data=>mutate(action,{...base,...data}));
  else if(action==='reassign'){
    const options=[{value:'',label:'Select a receiving session…',disabled:true},...activeSessions().map(session=>({value:session.id,label:sessionLabel(session.id)}))];
    openEditor('Reassign ownership',field('Receiving session','session_id','select','',options)+field('Reason / agreed handoff','reason','textarea'),data=>mutate(action,{...base,...data}));
  }else if(action==='reopen'){
    const options=[{value:'',label:'Select a receiving session…',disabled:true},...activeSessions().map(session=>({value:session.id,label:sessionLabel(session.id)}))];
    openEditor('Reopen & reassign completed task',
      '<p class="dialog-hint">The prior completion evidence stays attached. Reopening reacquires the recorded paths and starts a new work cycle with the selected agent.</p>'+
      field('Receiving session','session_id','select','',options)+
      field('What should happen next','next_step','textarea'),
      data=>mutate(action,{...base,...data}));
  }else if(action==='close')openEditor('Close task and release its claims',field('Outcome or cancellation reason','summary','textarea'),data=>mutate(action,{...base,...data}));
  else openEditor(action==='pause'?'Pause this task':'Resume this task',`<p>${esc(task.title)}</p><p>${action==='pause'?'Claims stay reserved. The agent must check in to see your pause.':'This releases your pause instruction; it does not start an agent automatically.'}</p>`,()=>mutate(action,base));
});

$('#tasks').addEventListener('toggle',event=>{
  if(!event.target.matches('.task-body'))return;
  const article=event.target.closest('[data-task]');
  if(article)taskDetailState.set(article.dataset.task,event.target.open);
},true);

$('#messages').addEventListener('click',async event=>{
  const reply=event.target.closest('[data-reply]');
  if(reply){const message=board.messages.find(item=>item.id===reply.dataset.reply);composeMessage(message?.task_id||'',message?.sender||'all',message?.id||'');return;}
  const button=event.target.closest('[data-ack]');
  if(!button)return;
  try{await mutate('ack',{message_id:button.dataset.ack});await refresh();}catch(error){$('#notice').textContent=error.message;}
});

$('.rail-tabs').addEventListener('click',event=>{
  const button=event.target.closest('[data-rail-tab]');
  if(!button)return;
  ui.rail=button.dataset.railTab;
  renderRail();
});

document.addEventListener('click',event=>{
  const pageButton=event.target.closest('[data-page-list]');
  if(pageButton&&!pageButton.disabled){ui[pageButton.dataset.pageList]=Number(pageButton.dataset.page);if(pageButton.dataset.pageList==='taskPage')renderTasks();else if(pageButton.dataset.pageList==='eventPage')renderEvents();else renderRail();}
  if(!event.target.closest('.notification-wrap')&&!$('#notification-panel').hidden){$('#notification-panel').hidden=true;$('#notifications').setAttribute('aria-expanded','false');}
});

$('.overview-strip').addEventListener('click',event=>{
  const button=event.target.closest('[data-quick-status]');
  if(!button)return;
  $('#status-filter').value=button.dataset.quickStatus;
  ui.taskPage=1;
  renderMetrics();renderTasks();
  $('#work-title').scrollIntoView({behavior:'smooth',block:'start'});
});

$('.top-nav').addEventListener('click',event=>{
  const link=event.target.closest('a');
  if(!link)return;
  if(link.getAttribute('href')==='#team-inbox'){ui.rail='inbox';renderRail();}
  if(link.getAttribute('href')==='#people-panel'){ui.rail='people';renderRail();}
  if(link.getAttribute('href')==='#activity-history')$('#activity-history').open=true;
});

$('#notifications').onclick=()=>{
  const panel=$('#notification-panel'),open=panel.hidden;
  panel.hidden=!open;
  $('#notifications').setAttribute('aria-expanded',String(open));
};
$('#mark-seen').onclick=()=>{
  notificationCursor=maxEventSeq();
  try{localStorage.setItem(notificationKey(project()),String(notificationCursor));}catch(error){}
  renderNotifications();
};

function resetTaskPage(){ui.taskPage=1;if(board){renderMetrics();renderTasks();}}
$('#refresh').onclick=refresh;
$('#project').addEventListener('change',()=>{Object.assign(ui,{taskPage:1,messagePage:1,notePage:1,sessionPage:1,eventPage:1});taskDetailState.clear();notificationProject='';refresh();});
$('#status-filter').onchange=resetTaskPage;
$('#owner-filter').onchange=resetTaskPage;
$('#priority-filter').onchange=resetTaskPage;
$('#sort-tasks').onchange=resetTaskPage;
$('#task-page-size').onchange=resetTaskPage;
$('#search').oninput=resetTaskPage;

refresh();
setInterval(()=>{if(!document.hidden&&!$('#editor').open)refresh();},3000);
