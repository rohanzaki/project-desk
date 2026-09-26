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
const FALLBACK_PROJECT='media-intelligence';
const LAST_PROJECT_KEY='project-desk:last-project';
let projects=[],currentProject='';
const project=()=>currentProject||FALLBACK_PROJECT;
const pathProject=()=>{const match=location.pathname.match(/^\/p\/([a-z0-9][a-z0-9-]*)\/?$/);return match?match[1]:'';};
const onOverview=()=>location.pathname==='/projects';
const taskFor=id=>board?.tasks.find(task=>task.id===id);
const sessionFor=id=>board?.sessions.find(session=>session.id===id);
const shortId=id=>id?String(id).slice(-8):'';
const name=id=>id==='rohan'?'Human':sessionFor(id)?.name||id||'Unassigned';
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
const agentTitle=id=>({rohan:'Human',codex:'Codex',claude:'Claude',all:'Everyone',unknown:'Unknown'}[agentKind(id)]||name(id));
const agentChip=(id,extra='')=>`<span class="agent-chip agent-${esc(agentKind(id))} ${extra}"><span class="agent-avatar">${esc(initials(id))}</span><span>${esc(agentTitle(id))}</span></span>`;
const sessionLabel=id=>{
  const session=sessionFor(id);
  return session?`${session.name} · ${session.branch} · ${shortId(session.id)}${session.imported?' · imported':''}`:name(id);
};
const recipientLabel=id=>({all:'Everyone',rohan:'Human',codex:'All Codex sessions',claude:'All Claude sessions'}[id]||sessionLabel(id));
const activeSessions=()=>board.sessions.filter(session=>!session.imported);
const notificationKey=projectName=>`project-desk:last-seen:${projectName}`;
const pinKey=projectName=>`project-desk:pinned-tasks:${projectName}`;
const significantEvent=event=>event&&(
  event.kind.startsWith('task.')||event.kind.startsWith('handoff.')||event.kind==='message.sent'||
  event.kind==='note.created'||event.kind==='changelog.published'||event.kind==='approval.requested'||
  event.kind==='deploy.recorded'||event.kind==='action.added');

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

function recipientOptions(value='all',include='',includeLabel=''){
  const options=[
    {value:'all',label:'Everyone'},
    {value:'rohan',label:'Human'},
    {value:'codex',label:'All Codex sessions'},
    {value:'claude',label:'All Claude sessions'}
  ];
  const ids=new Set(options.map(option=>option.value));
  [...activeSessions(),sessionFor(include)].filter(Boolean).forEach(session=>{
    if(!ids.has(session.id)){options.push({value:session.id,label:sessionLabel(session.id)});ids.add(session.id);}
  });
  (board?.crossovers||[]).filter(item=>item.status!=='closed').forEach(item=>{
    if(!ids.has(item.id)){options.push({value:item.id,label:`Crossover · ${item.title} (${item.members.map(member=>member.project).join(' + ')})`});ids.add(item.id);}
  });
  // A sender from another project is not in this board's sessions; keep it replyable.
  if(include&&!ids.has(include)){options.push({value:include,label:includeLabel||include});ids.add(include);}
  return options;
}

function rememberProject(slug){try{localStorage.setItem(LAST_PROJECT_KEY,slug);}catch(error){}}
function storedProject(){try{return localStorage.getItem(LAST_PROJECT_KEY)||'';}catch(error){return '';}}
function projectLabel(item){
  const unread=item.unread_human?` · ${item.unread_human} unread`:'';
  return `${item.name} · ${item.open_tasks} open${unread}`;
}
function renderProjectSelect(){
  const visible=projects.filter(item=>!item.hidden||item.slug===currentProject);
  $('#project').innerHTML=visible.map(item=>
    `<option value="${esc(item.slug)}"${item.slug===currentProject?' selected':''}>${esc(projectLabel(item))}</option>`
  ).join('')+'<option value="__new__">+ New project</option>';
}
async function loadProjects(){
  const response=await fetch('/api/projects');
  if(!response.ok)throw Error('Cannot list projects');
  projects=(await response.json()).projects;
}
function chooseInitialProject(){
  const known=slug=>projects.some(item=>item.slug===slug);
  const fromPath=pathProject();
  if(fromPath&&!known(fromPath)){
    $('#notice').textContent=`Unknown project “${fromPath}”. Pick one from the list, or create it with + New project.`;
  }
  currentProject=[fromPath,storedProject(),FALLBACK_PROJECT].find(slug=>slug&&known(slug))||projects[0]?.slug||FALLBACK_PROJECT;
  rememberProject(currentProject);
}
function showBoard(){
  $('#projects-overview').hidden=true;
  $('.overview-strip').hidden=false;
  $('.layout').hidden=false;
  $('#activity-history').hidden=false;
}
function renderOverview(){
  $('.overview-strip').hidden=true;
  $('.layout').hidden=true;
  $('#activity-history').hidden=true;
  $('#shared-locks').hidden=true;
  const section=$('#projects-overview');
  section.hidden=false;
  section.innerHTML='<h3>All projects</h3><div class="project-cards">'+projects.filter(item=>!item.hidden).map(item=>`
    <a class="project-card" href="/p/${esc(item.slug)}">
      <strong>${esc(item.name)}</strong><span class="mono">${esc(item.slug)}</span>
      <span>${item.open_tasks} open · ${item.live_sessions} live agents${item.unread_human?` · ${item.unread_human} unread`:''}</span>
      <small>${esc((item.repo_roots||[]).join(', ')||'No repo folder recorded')}</small>
      <small>${item.last_activity?'Last activity '+esc(time(item.last_activity)):'No activity yet'}</small>
    </a>`).join('')+'</div>';
}
function switchProject(slug){
  currentProject=slug;rememberProject(slug);
  history.pushState({},'',`/p/${slug}`);
  Object.assign(ui,{taskPage:1,messagePage:1,notePage:1,sessionPage:1,eventPage:1});
  taskDetailState.clear();notificationProject='';
  showBoard();renderProjectSelect();refresh();
}
const senderLabel=message=>message.from_project?`${message.from_name||message.sender} (project ${message.from_project})`:name(message.sender);
const crossoverFor=taskId=>(board?.crossovers||[]).find(item=>item.members.some(member=>member.task_id===taskId));
function showCrossoverLink(view){
  const readonly=(label,value)=>`<label>${esc(label)}<textarea readonly rows="3">${esc(value)}</textarea></label>`;
  openEditor(`Crossover ${view.id}`,
    readonly("Paste into the other project's Claude or Codex",view.paste_line)+
    `<p><a href="/x/${encodeURIComponent(view.id)}" target="_blank" rel="noopener">Open the join page</a> · waiting to join: ${esc((view.awaiting_join||[]).join(', ')||'nobody')}</p>`,
    async()=>{},'Done');
}
function renderCrossovers(){
  const box=$('#crossovers');
  if(onOverview()){box.hidden=true;return;}
  const dayAgo=Date.now()-864e5;
  const items=(board?.crossovers||[]).filter(item=>item.status!=='closed'||new Date(item.updated).getTime()>dayAgo);
  box.hidden=!items.length;
  box.innerHTML=items.length?'<strong>Crossovers with other projects</strong>'+items.map(item=>`<div class="crossover crossover-${esc(item.status)}"><span class="crossover-status">${esc(item.status)}</span> <strong>${esc(item.title)}</strong> <span class="meta">${esc(item.id)}${item.contract_version?` · contract v${esc(item.contract_version)}`:''}</span> <button type="button" class="crossover-link" data-crossover-link="${esc(item.id)}">Join link</button><div class="crossover-members">${item.members.map(member=>`<span class="crossover-member">${esc(member.project)}: ${member.task_id?`${esc(member.owner_name||shortId(member.owner))} · ${esc(shortId(member.task_id))} · ${esc(member.task_status||'')} · ${member.signed_off?'signed off':'awaiting sign-off'}`:'invited, not joined'}</span>`).join('')}</div></div>`).join(''):'';
}
function evidenceBadge(task){
  const summary=(board.evidence||{})[task.id];
  if(!summary)return '';
  return `<span class="evidence evidence-${esc(summary.level)}" title="${esc(summary.checks)} structured check(s) reported by the agent">${summary.level==='checked'?'checked':'failing'}</span>`;
}
function journalHtml(task){
  const entries=(board.task_logs||{})[task.id]||[];
  if(!entries.length)return '';
  return `<div class="journal"><span class="detail-label">Recent log</span>${entries.map(entry=>`<div class="journal-entry"><span>${esc(entry.kind)}</span> ${esc(entry.entry.slice(0,220))} <time>${time(entry.created)}</time></div>`).join('')}</div>`;
}
const RESOLVED_LABEL={done:'done',dropped:'cleared',superseded:'replaced'};
const actionGroupState=new Map();
function actionItemHtml(item,withTask=true){
  const done=item.status!=='open';
  const who=item.assignee==='rohan'?'you':item.assignee_name;
  const origin=item.origin_project&&item.origin_project!==project()?` · from ${esc(item.origin_project)}`:(item.project!==project()?` · for ${esc(item.project)}`:'');
  return `<label class="action-item${done?' action-done':''}"><input type="checkbox" data-action-item="${esc(item.id)}"${done?' checked':''}><span><span class="action-body">${esc(item.body)}</span><span class="meta">for ${esc(who)} · ${esc(item.author_name)} · ${time(item.created)}${withTask&&item.task_title?` · ${esc(item.task_title.slice(0,60))}`:''}${origin}${done&&item.resolved_by_name?` · ${esc(RESOLVED_LABEL[item.status]||item.status)} by ${esc(item.resolved_by_name)}`:''}${item.resolution?` — ${esc(item.resolution)}`:''}</span></span></label>`;
}
function shownActionItems(){
  const filter=$('#action-filter').value,all=board.action_items||[];
  return all.filter(item=>filter==='done'?item.status!=='open':item.status==='open'&&(filter==='all'||(filter==='human'?item.assignee==='rohan':item.assignee!=='rohan')));
}
// One group per task, so what is left reads as each task's own list. Live tasks
// first, then finished ones (folded, easy to clear), then items with no task.
function actionGroups(items){
  const groups=new Map();
  for(const item of items){
    const key=item.task_id||'';
    if(!groups.has(key))groups.set(key,{key,title:item.task_id?(item.task_title||item.task_id):'Not linked to a task',status:item.task_id?(item.task_status||''):'',items:[]});
    groups.get(key).items.push(item);
  }
  const rank=group=>!group.key?2:group.status==='DONE'?1:0;
  const latest=group=>Math.max(...group.items.map(item=>Date.parse(item.created)));
  return [...groups.values()].sort((a,b)=>rank(a)-rank(b)||latest(b)-latest(a));
}
function actionGroupHtml(group,canClear){
  const open=actionGroupState.has(group.key)?actionGroupState.get(group.key):group.status!=='DONE';
  const count=`${group.items.length} item${group.items.length===1?'':'s'}`;
  return `<details class="action-group" data-action-group="${esc(group.key)}"${open?' open':''}><summary>${group.status?`<span class="badge ${esc(group.status.toLowerCase())}">${esc(group.status)}</span>`:''}<span class="action-group-title">${esc(group.title)}</span><span class="meta">${count}</span>${canClear?`<button type="button" data-clear-items="${esc(group.items.map(item=>item.id).join(','))}" data-clear-label="${esc(group.title.slice(0,80))}" title="Clear this task's action points">Clear</button>`:''}</summary>${group.items.map(item=>actionItemHtml(item,false)).join('')}</details>`;
}
function renderActionItems(){
  const box=$('#action-items');
  if(onOverview()){box.hidden=true;return;}
  box.hidden=false;
  const all=board.action_items||[],shown=shownActionItems(),resolvedView=$('#action-filter').value==='done';
  const openForYou=all.filter(item=>item.status==='open'&&item.assignee==='rohan').length;
  $('#action-filter').options[0].textContent=`For you${openForYou?` (${openForYou})`:''}`;
  $('#clear-actions').hidden=resolvedView||!shown.length;
  $('#action-list').innerHTML=shown.length?actionGroups(shown).map(group=>actionGroupHtml(group,!resolvedView)).join(''):'<p class="empty">Nothing here. Agents add what they leave for you when they finish.</p>';
}
function taskActionsHtml(task){
  const items=(board.action_items||[]).filter(item=>item.task_id===task.id&&['open','done'].includes(item.status));
  if(!items.length)return '';
  const open=items.filter(item=>item.status==='open');
  const clear=open.length?`<button type="button" data-clear-items="${esc(open.map(item=>item.id).join(','))}" data-clear-label="${esc(task.title.slice(0,80))}">Clear all</button>`:'';
  return `<div class="task-actions-list"><span class="detail-label">Action points ${clear}</span>${items.map(item=>actionItemHtml(item,false)).join('')}</div>`;
}
async function clearActionItems(ids,label){
  if(!ids.length)return;
  const result=await mutate('action.clear',{item_ids:ids});
  const failed=Object.keys(result.failed||{}).length;
  $('#notice').textContent=`Cleared ${result.resolved.length} action point${result.resolved.length===1?'':'s'}${label?` from ${label}`:''}. They are under "Recently done"; untick one to bring it back.${failed?` ${failed} could not be cleared.`:''}`;
  await refresh();
}
function renderApprovals(){
  const box=$('#approvals');
  if(onOverview()){box.hidden=true;return;}
  const pending=(board.approvals||[]).filter(item=>item.status==='pending');
  box.hidden=!pending.length;
  box.innerHTML=pending.length?'<h3>Decisions waiting for you</h3>'+pending.map(item=>`<article class="approval" data-approval="${esc(item.id)}"><div><strong>${esc(item.title)}</strong> <span class="meta">${esc(item.requester_name)} · ${time(item.created)}</span></div><p>${esc(item.context)}</p><div class="approval-options">${item.options.map(option=>`<button type="button" data-decision="${esc(option)}">${esc(option)}</button>`).join('')}<button type="button" data-decision="" class="approval-other">Other…</button></div></article>`).join(''):'';
}
function renderStale(){
  const box=$('#stale-claims');
  if(onOverview()){box.hidden=true;return;}
  const stale=board.stale_claims||[];
  box.hidden=!stale.length;
  box.innerHTML=stale.length?`<strong>Stale claims</strong> · owner silent 6h+ (use Reassign or Close on the task, or let the owner resume): `+stale.map(item=>`<span class="stale-item">${esc(shortId(item.task_id))} ${esc(item.title.slice(0,70))} · ${esc(item.owner_name.slice(0,40))} since ${time(item.owner_last_seen)}</span>`).join(''):'';
}
function renderProd(){
  const box=$('#prod-state');
  if(onOverview()){box.hidden=true;return;}
  const prod=board.prod||[],queues=(board.queues||[]).filter(item=>item.queue.length);
  box.hidden=!prod.length&&!queues.length;
  box.innerHTML=(prod.length?'<strong>On prod</strong> '+prod.map(item=>`<span class="prod-item">${esc(item.service)} ${esc(String(item.commit_ref).slice(0,12))} · ${esc(item.by)} · ${time(item.created)}</span>`).join(''):'')+
    (queues.length?'<div><strong>Queues</strong> '+queues.map(item=>`<span class="prod-item">${esc(item.resource)}: ${item.queue.map(entry=>esc(entry.name)).join(' → ')}</span>`).join('')+'</div>':'');
}
function renderLessons(){
  const lessons=board.lessons||[];
  $('#lesson-tab-count').textContent=lessons.length||'';
  $('#lessons').innerHTML=lessons.length?lessons.map(item=>`<article class="lesson" data-lesson="${esc(item.id)}"><p>${esc(item.body)}</p><div class="meta">${esc(item.author_name)} · ${esc(item.scope)}${item.paths.length?' · '+item.paths.map(esc).join(', '):''}${item.tags.length?' · #'+item.tags.map(esc).join(' #'):''}</div><button type="button" data-archive-lesson="${esc(item.id)}">Archive</button></article>`).join(''):'<p class="empty">No lessons yet. Agents add them with remember; add one here too.</p>';
}
function renderSharedLocks(){
  const box=$('#shared-locks');
  if(onOverview()){box.hidden=true;return;}
  const locks=board?.shared_locks||[];
  box.hidden=!locks.length;
  box.textContent=locks.length?'Shared locks held in other projects: '+locks.map(lock=>`${lock.resource} (${lock.project}, ${shortId(lock.task_id)})`).join(' · '):'';
}
function openNewProject(){
  openEditor('New project',
    field('Name','name')+field('Short name (lowercase, dashes)','slug')+
    field('Repo folders (one absolute path per line)','repo_roots','textarea','',[],false),
    async data=>{
      const slug=(data.slug||data.name).trim().toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'');
      const roots=(data.repo_roots||'').split('\n').map(line=>line.trim()).filter(Boolean);
      if(!roots.length)throw Error('Add at least one repo folder (absolute path).');
      await mutate('project.create',{slug,name:data.name.trim(),repo_roots:roots});
      await loadProjects();
      switchProject(slug);
      setTimeout(()=>openConnect(slug),0);
    },'Create project');
}
async function openConnect(slug=project()){
  const response=await fetch(`/api/projects/${encodeURIComponent(slug)}/connect`);
  const info=await response.json();
  if(!response.ok){$('#notice').textContent=info.error||'Cannot load connection details';return;}
  const readonly=(label,value)=>`<label>${esc(label)}<textarea readonly rows="2">${esc(value)}</textarea></label>`;
  openEditor(`Connect agents · ${info.name}`,
    readonly('Paste into Claude or Codex, in this repo',info.paste_line)+
    readonly('Or run in the repo folder',info.join_command)+
    `<p><a href="${esc(info.kit_url)}">Download the setup kit (zip)</a> · <a href="${esc(info.onboard_url)}" target="_blank" rel="noopener">Open the agent instructions</a></p>`,
    async()=>{},'Done');
}
function openProjectSettings(){
  const item=projects.find(entry=>entry.slug===project());
  if(!item)return;
  openEditor(`Project settings · ${item.name}`,
    field('Name','name','text',item.name)+
    field('Repo folders (one absolute path per line)','repo_roots','textarea',(item.repo_roots||[]).join('\n'),[],false)+
    field('Rules file (optional absolute path)','rules_path','text',item.rules_path||'',[],false),
    async data=>{
      await mutate('project.update',{slug:item.slug,name:data.name.trim(),
        repo_roots:(data.repo_roots||'').split('\n').map(line=>line.trim()).filter(Boolean),
        rules_path:(data.rules_path||'').trim()});
      await loadProjects();renderProjectSelect();
    });
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

function openEditor(title,fieldsHtml,action,saveLabel='Save'){
  $('#editor-title').textContent=title;
  $('#editor-fields').innerHTML=fieldsHtml;
  $('#form-error').textContent='';
  $('#save').textContent=saveLabel;
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
    <div class="meta message-direction">${esc(senderLabel(message))} to ${esc(recipientLabel(message.recipient))}${message.crossover_id?` · crossover ${esc(message.crossover_id)}`:''}</div>
    ${task?`<div class="message-task">Task: <strong>${esc(task.title)}</strong> <span>${esc(shortId(task.id))}</span></div>`:''}
    ${message.kind&&message.kind!=='message'?`<span class="kind-badge kind-${esc(message.kind)}">${esc(message.kind)}${message.question_status?` · ${esc(message.question_status)}`:''}</span>`:''}
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
      <div class="task-state"><span class="badge ${task.status.toLowerCase()}">${task.status}</span>${evidenceBadge(task)}${task.priority!=='normal'?`<span class="priority">${esc(task.priority)}</span>`:''}</div>
      <p class="task-next"><span>${task.status==='DONE'?'Outcome':'Next'}</span>${esc(task.status==='DONE'?(task.summary||'No completion summary recorded'):(task.next_step||'No next step recorded'))}</p>
    </div>
    <details class="task-body"${expanded?' open':''}>
      <summary><span>Task details</span><span>${task.resources.length} path${task.resources.length===1?'':'s'} · ${comments} comment${comments===1?'':'s'}</span></summary>
      <div class="task-detail-grid">
        <div><span class="detail-label">Scope</span><p class="scope">${task.resources.map(esc).join(' / ')||'No paths recorded'}</p></div>
        <div><span class="detail-label">Evidence</span><pre>${esc('Task: '+task.id+'\nValidation: '+(task.validation||'Not recorded')+'\nCommit: '+(task.commit_ref||'Not recorded')+'\nDeployment: '+task.deployment+'\nWorktree: '+(board.sessions.find(session=>session.id===task.owner)?.worktree||'Not claimed')+'\nBranch: '+(board.sessions.find(session=>session.id===task.owner)?.branch||'Not claimed'))}</pre></div>
      </div>
      ${task.imported?`<div class="imported">${task.status==='DONE'?'Imported completion report':'Imported report — ownership needs confirmation'}</div>`:''}
      ${taskActionsHtml(task)}
      ${journalHtml(task)}
      ${crossoverFor(task.id)?`<div class="imported">In crossover ${esc(crossoverFor(task.id).id)} with ${esc(crossoverFor(task.id).members.filter(member=>member.task_id!==task.id).map(member=>member.project).join(', ')||'nobody yet')} · ${esc(crossoverFor(task.id).status)}</div>`:''}
      ${task.pending_owner?`<div class="imported">Handoff offered to ${esc(name(task.pending_owner))}; awaiting acceptance</div>`:''}
      ${handoffBrief(task)}
      ${discussion(task)}
      ${task.status==='DONE'
        ? '<div class="actions"><button class="reopen-action" data-action="reopen">Reopen &amp; reassign</button></div>'
        : `<div class="actions"><button data-action="${task.human_paused?'resume':'pause'}">${task.human_paused?'Resume':'Pause'}</button><button data-action="priority">Set priority</button><button data-action="reassign">Reassign</button><button data-action="crossover">Cross over</button><button data-action="close">Close with note</button></div>`}
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
  renderSharedLocks();
  renderCrossovers();
  renderApprovals();
  renderActionItems();
  renderStale();
  renderProd();
  renderLessons();
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
  const foreign=original?.from_project?`${original.from_name||original.sender} · project ${original.from_project}`:'';
  openEditor(title,context+field('Recipient','recipient','select',recipient,recipientOptions(recipient,original?.sender,foreign))+field('Message','body','textarea'),data=>mutate('message',{recipient:data.recipient,body:data.body,...(taskId?{task_id:taskId}:{}),...(replyTo?{reply_to:replyTo}:{})}));
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
  }else if(action==='crossover'){
    const existing=crossoverFor(task.id);
    const others=projects.filter(item=>item.slug!==project()&&!item.hidden&&!(existing?.members||[]).some(member=>member.project===item.slug));
    if(!others.length&&existing){showCrossoverLink(existing);return;}
    const options=[{value:'',label:'Select the other project…',disabled:true},...others.map(item=>({value:item.slug,label:item.name}))];
    openEditor(existing?`Invite another project to ${existing.id}`:'Cross over into another project',
      '<p class="dialog-hint">The other project joins with its own task in its own repo. You get a line to paste into that project\'s Claude or Codex.</p>'+
      field('Other project','invite','select','',options)+field('Note for them (optional)','note','textarea','',[],false),
      async data=>{const view=await mutate('crossover.start',{task_id:task.id,invite:[data.invite],note:data.note||''});setTimeout(()=>showCrossoverLink(view),0);},'Create link');
  }else if(action==='close')openEditor('Close task and release its claims',field('Outcome or cancellation reason','summary','textarea'),data=>mutate(action,{...base,...data}));
  else openEditor(action==='pause'?'Pause this task':'Resume this task',`<p>${esc(task.title)}</p><p>${action==='pause'?'Claims stay reserved. The agent must check in to see your pause.':'This releases your pause instruction; it does not start an agent automatically.'}</p>`,()=>mutate(action,base));
});

$('#approvals').addEventListener('click',event=>{
  const button=event.target.closest('[data-decision]');
  if(!button)return;
  const approval=button.closest('[data-approval]').dataset.approval,decision=button.dataset.decision;
  if(decision){mutate('approval.decide',{approval_id:approval,decision}).then(refresh).catch(error=>{$('#notice').textContent=error.message;});return;}
  openEditor('Another answer',field('Your decision','decision')+field('Note for the agent','note','textarea'),data=>mutate('approval.decide',{approval_id:approval,decision:data.decision,note:data.note}),'Send decision');
});
document.addEventListener('change',event=>{
  const box=event.target.closest('[data-action-item]');
  if(!box)return;
  mutate('action.resolve',{item_id:box.dataset.actionItem,status:box.checked?'done':'open'}).then(refresh).catch(error=>{$('#notice').textContent=error.message;box.checked=!box.checked;});
});
$('#action-filter').addEventListener('change',renderActionItems);
$('#action-list').addEventListener('toggle',event=>{
  const group=event.target.closest?.('[data-action-group]');
  if(group)actionGroupState.set(group.dataset.actionGroup,group.open);
},true);
document.addEventListener('click',event=>{
  const button=event.target.closest('[data-clear-items]');
  if(!button)return;
  event.preventDefault();
  button.disabled=true;
  clearActionItems(button.dataset.clearItems.split(',').filter(Boolean),button.dataset.clearLabel)
    .catch(error=>{$('#notice').textContent=error.message;button.disabled=false;});
});
$('#clear-actions').addEventListener('click',()=>{
  const shown=shownActionItems(),dayAgo=days=>Date.now()-days*864e5;
  const choices=[
    ['finished','On finished (DONE) tasks',shown.filter(item=>item.task_status==='DONE')],
    ['old','Older than 3 days',shown.filter(item=>Date.parse(item.created)<dayAgo(3))],
    ['unlinked','Not linked to a task',shown.filter(item=>!item.task_id)],
    ['shown','Everything in this list',shown]];
  const options=choices.map(([value,label,items])=>({value,label:`${label} (${items.length})`,disabled:!items.length}));
  const first=options.find(option=>!option.disabled)?.value||'shown';
  openEditor('Clear action points',
    '<p class="dialog-hint">Cleared points move to "Recently done" for 3 days; untick one there to bring it back.</p>'+
    field('Which','which','select',first,options),
    data=>{const pick=choices.find(([value])=>value===data.which);return clearActionItems((pick?pick[2]:[]).map(item=>item.id),'');},'Clear');
});
$('#announce-restart').addEventListener('click',()=>openEditor('Announce a Project Desk restart',
  '<p class="dialog-hint">Posts the notice to every active project\'s board. The desk posts "Project Desk is back" by itself after it starts. VS Code sessions that are not connected to the desk do not see this.</p>'+
  field('Starts in (seconds)','seconds','text','60')+field('Reason (optional)','reason','text','',[],false),
  async data=>{const result=await mutate('desk.announce',data);$('#notice').textContent='Restart announced to: '+Object.keys(result.announced_to||{}).join(', ');},'Announce'));
$('#new-action').addEventListener('click',()=>openEditor('Add action points',
  field('One per line','body','textarea')+
  field('For','assignee','select','human',[{value:'human',label:'Me (the human)'},{value:'agents',label:'Any agent in this project'}]),
  data=>mutate('action.add',data),'Add'));
$('#lessons').addEventListener('click',event=>{
  const button=event.target.closest('[data-archive-lesson]');
  if(!button)return;
  openEditor('Archive lesson',field('Why it no longer applies','reason','textarea'),data=>mutate('lesson.archive',{lesson_id:button.dataset.archiveLesson,reason:data.reason}),'Archive');
});
$('#new-lesson').addEventListener('click',()=>openEditor('Add a lesson for every agent',
  field('Lesson (a few sentences, with the why)','body','textarea')+field('Paths it applies to (one per line, optional)','paths','textarea','',[],false)+
  field('Tags (comma separated, optional)','tags','text','',[],false)+field('Scope','scope','select','project',[{value:'project',label:'This project'},{value:'global',label:'Every project'}]),
  data=>mutate('lesson.create',data),'Save lesson'));

$('#crossovers').addEventListener('click',event=>{
  const button=event.target.closest('[data-crossover-link]');
  if(!button)return;
  const view=(board?.crossovers||[]).find(item=>item.id===button.dataset.crossoverLink);
  if(view)showCrossoverLink(view);
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
$('#project').addEventListener('change',event=>{
  const value=event.target.value;
  if(value==='__new__'){renderProjectSelect();openNewProject();return;}
  switchProject(value);
});
$('#connect-agents').onclick=()=>openConnect();
$('#project-settings').onclick=openProjectSettings;
$('#status-filter').onchange=resetTaskPage;
$('#owner-filter').onchange=resetTaskPage;
$('#priority-filter').onchange=resetTaskPage;
$('#sort-tasks').onchange=resetTaskPage;
$('#task-page-size').onchange=resetTaskPage;
$('#search').oninput=resetTaskPage;

async function boot(){
  try{await loadProjects();}
  catch(error){projects=[{slug:FALLBACK_PROJECT,name:'Media Intelligence',open_tasks:0,unread_human:0,live_sessions:0,hidden:false,repo_roots:[]}];}
  chooseInitialProject();renderProjectSelect();
  if(onOverview())renderOverview();else showBoard();
  refresh();
}
boot();
setInterval(()=>{if(!document.hidden)loadProjects().then(()=>{renderProjectSelect();if(onOverview())renderOverview();}).catch(()=>{});},30000);
window.addEventListener('popstate',()=>{chooseInitialProject();renderProjectSelect();if(onOverview())renderOverview();else showBoard();refresh();});
setInterval(()=>{if(!document.hidden&&!$('#editor').open)refresh();},3000);
