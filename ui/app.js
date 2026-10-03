'use strict';
const $=id=>document.getElementById(id);
let state=null,page='board',searchResult=null,activeJob=null,polling=false;
const preview=new URLSearchParams(location.search).get('preview')==='1';
function node(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;}
function toast(message){$('toast').textContent=message;$('toast').classList.add('visible');clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').classList.remove('visible'),4000);}
async function api(path,data){
 if(preview){toast('Design preview: changes are disabled. Run the backend for live functions.');throw new Error('Preview mode');}
 const options=data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-WhiteBoard-Token':state?.csrf||''},body:JSON.stringify(data)};
 const response=await fetch('/api/'+path,options);const value=await response.json();
 if(!response.ok)throw new Error(value.error||'Request failed');return value;
}
function dateString(ts){if(!ts)return 'Not opened yet';return new Date(ts*1000).toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZone:state?.config?.timezone||'America/Chicago'});}
function fileName(path){return path.split('/').pop();}
function title(file){return fileName(file.path).replace(/\.[^.]+$/,'').replace(/[_-]/g,' ');}
function showPage(next){page=next;searchResult=null;document.querySelectorAll('[data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===page));
 const labels={board:['My whiteboard','Room to think.','An operating system that remembers the context.'],library:['File library','Everything, connected.','Find files by what they mean, not just what they are called.'],activity:['Activity','A little trail of thought.','Documents you opened here, in the order you found them.'],tasks:['Agent tasks','Goals into motion.','Review work, inspect results, and resume interrupted tasks.']};
 const l=labels[page];$('breadcrumb').textContent=l[0];$('pageTitle').textContent=l[1];$('pageSubtitle').textContent=l[2];
 $('boardLayout').hidden=!['board','library'].includes(page);$('activityPage').hidden=page!=='activity';$('tasksPage').hidden=page!=='tasks';
 $('searchEvidence').hidden=true;render();if(page==='tasks')loadTasks();}
function renderCards(){
 if(!state)return;let files=state.files||[];
 if(searchResult){const matches=new Map(searchResult.matches.map(m=>[m.file_id,m]));files=files.filter(f=>matches.has(f.id)).sort((a,b)=>matches.get(b.id).score-matches.get(a.id).score);}
 else if(page==='board'){const pins=files.filter(f=>f.pinned);files=pins.length?pins:files.slice(0,6);}
 $('sectionTitle').replaceChildren(document.createTextNode(searchResult?'Found in your workspace':page==='library'?'All files':'On your board'),node('span',String(files.length)));
 const cards=$('cards');cards.replaceChildren();
 if(!files.length){cards.append(node('div',searchResult?'No matching files in that context. Try a broader topic or another day.':'Your board is ready. Add a document or leave a note.','empty'));return;}
 files.forEach(file=>{const card=node('article',undefined,'file-card');const top=node('div',undefined,'card-top');
 top.append(node('span',file.path.split('.').pop().toUpperCase(),'file-type'));
 const pin=node('button',file.pinned?'●':'○','pin-button'+(file.pinned?' on':''));pin.title=file.pinned?'Unpin file':'Pin to whiteboard';pin.setAttribute('aria-label',pin.title);pin.onclick=async()=>{try{await api('pin',{file_id:file.id,pinned:!file.pinned});await refresh();}catch(e){if(!preview)toast(e.message);}};top.append(pin);
 const h=node('h3',title(file));const p=node('p',file.summary||file.description||'Waiting for a local summary.');
 const bottom=node('div',undefined,'card-bottom');bottom.append(node('span',file.metadata_status==='ready'?'LOCAL SUMMARY':file.metadata_status==='error'?'TEXT EXCERPT · MODEL RETRY NEEDED':'TEXT EXCERPT'));
 const open=node('button','↗','open-file');open.setAttribute('aria-label','Open '+fileName(file.path));open.onclick=()=>openFile(file.id);bottom.append(open);card.append(top,h,p,bottom);cards.append(card);});
}
function renderActivity(){const wrap=$('activityPage');wrap.replaceChildren();const events=state?.timeline||[];
 if(!events.length){wrap.append(node('div','Open a document in WhiteBoardOS to begin your activity history.','empty'));return;}
 events.forEach(e=>{const row=node('div',undefined,'list-row');row.append(node('span','▤','recent-icon'));const detail=node('div');detail.append(node('h3',e.path?fileName(e.path):'Removed document'),node('p','Opened in the WhiteBoardOS viewer · human activity'));row.append(detail,node('small',dateString(e.timestamp)));if(e.path){const b=node('button','Open ↗','button pale');b.onclick=()=>openFile(e.file_id);row.append(b);}wrap.append(row);});}
function renderRecent(){const wrap=$('recentList');wrap.replaceChildren();const events=(state?.timeline||[]).slice(0,3);
 if(!events.length){wrap.append(node('p','Your next opened file starts the trail.','field-note'));return;}
 events.forEach(e=>{const b=node('button',undefined,'recent-item');b.append(node('span','▤','recent-icon'));const detail=node('div');detail.append(node('strong',e.path?fileName(e.path):'Removed document'),node('small',dateString(e.timestamp)));b.append(detail);b.onclick=()=>e.path&&openFile(e.file_id);wrap.append(b);});}
function render(){if(!state)return;renderCards();renderRecent();renderActivity();
 const total=state.files.length,ready=state.metadata_ready||0;
 $('metadataProgress').style.width=(total?ready/total*100:0)+'%';$('metadataCount').textContent=ready+' of '+total+' documents described';
 const status=state.model?.status||'unavailable';$('modelStatus').textContent=({ready:'Local model ready',indexing:'Describing your documents',unavailable:'Local model unavailable',checking:'Checking local model',preview:'Design preview'})[status]||status;
 $('modelDot').className='status-dot'+(status==='unavailable'?' off':status==='indexing'?' busy':'');
 $('modelDetail').textContent=state.model?.last_error||'Ollama · Qwen2.5 0.5B';$('settingsModelStatus').textContent=$('modelStatus').textContent;
 $('systemStatus').textContent=preview?'Design preview · sample data':'Workspace connected';$('systemDot').className='status-dot'+(preview?' off':'');
 $('deviceLabel').textContent=state.system?.architecture?state.system.architecture+' · local workspace':'Raspberry Pi · local workspace';
 $('footerMetric').textContent=total+' files indexed · '+ready+' local summaries';
 if(activeJob){const job=state.jobs.find(j=>j.id===activeJob);if(job)renderJob(job);}}
async function refresh(){if(polling)return;polling=true;try{state=await api('status');$('connectionBanner').hidden=true;render();}catch(e){if(!preview){$('connectionBanner').hidden=false;$('connectionBanner').textContent='Workspace offline. Start the WhiteBoardOS service, then refresh this window.';$('systemStatus').textContent='Backend unavailable';$('systemDot').className='status-dot off';}}finally{polling=false;}}
async function search(query,range='',type=''){try{searchResult=await api('search',{query,time_range:range,file_type:type,activity:range?'human_view':''});page='library';$('boardLayout').hidden=false;$('activityPage').hidden=true;$('tasksPage').hidden=true;renderCards();$('searchEvidence').hidden=false;
 const w=searchResult.time_window;$('searchEvidence').textContent=w?'Human-view events between '+w.start+' and '+w.end+'. '+searchResult.method:searchResult.method;}catch(e){if(!preview)toast(e.message);}}
function querySearch(){const raw=$('goalInput').value.trim();if(!raw)return toast('Give me a topic to look for.');const range=/\byesterday\b/i.test(raw)?'yesterday':/\btoday\b/i.test(raw)?'today':'';const type=/\bpdf\b/i.test(raw)?'pdf':'';
 const query=raw.replace(/\b(find|that|the|pdf|i|was|looking|at|opened|yesterday|today|about|me|a|file)\b/gi,' ').replace(/\s+/g,' ').trim()||raw;search(query,range,type);}
async function openFile(id){if(preview)return toast('Sample document in design preview. Import real files in the running OS.');
 try{const file=state.files.find(f=>f.id===id);if(!file)return;await api('view',{file_id:id});$('viewerTitle').textContent=fileName(file.path);$('viewerMeta').textContent=file.summary||file.description;$('documentFrame').src='/document/'+encodeURIComponent(id);$('relatedFiles').replaceChildren();
 const related=await api('search',{query:file.project||title(file),limit:4});const matches=related.matches.filter(m=>m.file_id!==id);
 if(matches.length){$('relatedFiles').append(node('span','Also matching this topic: '));matches.forEach(m=>{const b=node('button',fileName(m.path)+' ↗','button pale');b.onclick=()=>openFile(m.file_id);$('relatedFiles').append(b);});}
 $('viewer').showModal();await refresh();}catch(e){toast(e.message);}}
async function startTask(){const goal=$('goalInput').value.trim();if(!goal)return toast('Write a goal first.');try{const response=await api('task',{goal});activeJob=response.job_id;$('taskGoal').textContent=goal;$('taskState').textContent='Working with Gemini…';$('taskAnswer').textContent='';$('taskMetrics').textContent='';$('approval').hidden=true;$('agentDialog').showModal();await refresh();}catch(e){if(!preview)toast(e.message);}}
function renderJob(job){$('taskState').textContent=job.status==='approval'?'Waiting for your review':job.status==='running'?'Working with Gemini…':job.status==='done'?'Task completed':'Task paused';$('taskAnswer').textContent=job.answer||job.error||'';$('approval').hidden=job.status!=='approval';if(job.pending)$('approvalDetails').textContent=JSON.stringify(job.pending,null,2);const m=job.metrics||{};$('taskMetrics').textContent=m.elapsed_seconds!==undefined?m.elapsed_seconds+' seconds · '+m.tool_calls+' tool calls · usage '+JSON.stringify(m.usage):'';}
async function decide(approve){try{await api('approval',{job_id:activeJob,approve});await refresh();}catch(e){toast(e.message);}}
async function loadTasks(){try{const {tasks}=await api('tasks');const wrap=$('tasksPage');wrap.replaceChildren();if(!tasks.length)wrap.append(node('div','A clear goal is the beginning of a task. Ask Gemini above.','empty'));tasks.reverse().forEach(t=>{const row=node('div',undefined,'list-row');const detail=node('div');detail.append(node('h3',(t.goal||'Task').split('\n').slice(-1)[0]),node('p',t.answer||'Saved progress available'));row.append(detail,node('span',t.status,'badge'));if(t.status!=='done'){const b=node('button','Resume ↗','button pale');b.onclick=async()=>{try{const r=await api('task',{goal:'',task_id:t.id});activeJob=r.job_id;$('taskGoal').textContent=t.goal;$('agentDialog').showModal();refresh();}catch(e){toast(e.message);}};row.append(b);}wrap.append(row);});}catch(e){if(!preview)toast(e.message);}}
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>showPage(b.dataset.page));document.querySelectorAll('.close-dialog').forEach(b=>b.onclick=()=>{const d=b.closest('dialog');d.close();if(d.id==='viewer')$('documentFrame').src='about:blank';});
$('findBtn').onclick=querySearch;$('goalInput').onkeydown=e=>{if(e.key==='Enter')querySearch();};$('agentBtn').onclick=startTask;
document.querySelectorAll('[data-query]').forEach(b=>b.onclick=()=>{ $('goalInput').value=b.dataset.query;search(b.dataset.query,b.dataset.time||'',b.dataset.type||'');});
$('showAllBtn').onclick=()=>showPage('library');$('activityBtn').onclick=()=>showPage('activity');$('refreshBtn').onclick=async()=>{try{await api('index',{});toast('Index refresh queued. Local summaries follow in the background.');}catch(e){if(!preview)toast(e.message);}};
$('settingsBtn').onclick=()=>{if(state){$('geminiModel').value=state.config.gemini_model;$('timezone').value=state.config.timezone;$('apiKey').placeholder=state.config.gemini_configured?'Key configured · leave blank to retain':'Paste a Gemini API key';}$('settings').showModal();};
$('settingsForm').onsubmit=async e=>{e.preventDefault();try{await api('config',{api_key:$('apiKey').value,gemini_model:$('geminiModel').value,timezone:$('timezone').value});$('apiKey').value='';$('settings').close();toast('Settings saved locally.');refresh();}catch(error){if(!preview)toast(error.message);}};
const note=()=>$('noteDialog').showModal();$('newNoteBtn').onclick=note;$('addNote').onclick=note;
$('noteForm').onsubmit=async e=>{e.preventDefault();try{let path=$('noteName').value.trim();if(!/\.(txt|md)$/i.test(path))path+='.md';await api('create',{path,content:$('noteContent').value});$('noteDialog').close();$('noteForm').reset();toast('Note saved.');refresh();}catch(error){if(!preview)toast(error.message);}};
$('approveBtn').onclick=()=>decide(true);$('denyBtn').onclick=()=>decide(false);
$('importBtn').onclick=()=>$('fileInput').click();$('fileInput').onchange=async()=>{for(const file of $('fileInput').files){if(file.size>8*1024*1024){toast('Import limit: 8 MiB per file.');continue;}try{const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));await api('import',{name:file.name,content:btoa(binary)});toast('Added '+file.name);}catch(e){if(!preview)toast(e.message);}}$('fileInput').value='';refresh();};
function clock(){const now=new Date();$('clock').textContent=now.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'});$('dateLabel').textContent=now.toLocaleDateString([], {weekday:'long',month:'long',day:'numeric'}).toUpperCase()+' · YOUR PERSONAL SPACE';}clock();setInterval(clock,30000);
if(preview){state={csrf:'',config:{timezone:'America/Chicago',gemini_model:'gemini-2.5-flash',gemini_configured:false},model:{status:'preview'},system:{architecture:'Pi 4B · 4 GB'},metadata_ready:4,jobs:[],timeline:[{file_id:'p',path:'Raspberry_Pi_Audio.pdf',timestamp:Date.now()/1000-86400},{file_id:'a',path:'Atlas_proposal_v2.txt',timestamp:Date.now()/1000-4200}],files:[{id:'p',path:'Raspberry_Pi_Audio.pdf',summary:'A practical guide to USB microphones and recording audio on Raspberry Pi.',metadata_status:'ready',pinned:true},{id:'a',path:'Atlas_proposal_v2.txt',summary:'The updated Atlas hardware proposal, with a revised budget and milestones.',metadata_status:'ready',pinned:true},{id:'n',path:'A_few_things_to_try.md',summary:'Small experiments for a workspace that understands how you think.',metadata_status:'ready',pinned:true},{id:'r',path:'Research_connections.md',summary:'Notes on local metadata, document relationships, and useful context.',metadata_status:'ready',pinned:true}]};$('connectionBanner').hidden=false;$('connectionBanner').textContent='DESIGN PREVIEW · sample documents and activity · backend and model are not running';render();}else{refresh();setInterval(refresh,3000);}

$('scanWifi').onclick=async()=>{try{const result=await api('wifi');$('wifiNetworks').replaceChildren();(result.networks||[]).forEach(n=>{const o=node('option');o.value=n.ssid;o.label=n.signal+'% · '+n.security;$('wifiNetworks').append(o);});toast(result.error||'Networks available in the name field.');}catch(e){if(!preview)toast(e.message);}};
$('wifiForm').onsubmit=async e=>{e.preventDefault();try{toast('Connecting…');await api('wifi-connect',{ssid:$('wifiSsid').value,password:$('wifiPassword').value});$('wifiPassword').value='';toast('Network connected.');}catch(error){if(!preview)toast(error.message);}};
async function power(action){if(!confirm(action==='poweroff'?'Shut down WhiteBoardOS? Save your work first.':'Restart WhiteBoardOS? Save your work first.'))return;try{await api('power',{action,confirm:action});toast(action==='poweroff'?'Shutting down…':'Restarting…');}catch(e){if(!preview)toast(e.message);}}
$('powerBtn').onclick=()=>power('poweroff');$('rebootBtn').onclick=()=>power('reboot');
