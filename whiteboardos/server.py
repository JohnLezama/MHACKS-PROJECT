"""Loopback desktop backend; no external listener or browser-accessible shell."""
import argparse
import datetime as dt
import hashlib
import json
import mimetypes
import os
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
from .agent import Runner,Gemini,save
from .catalog import Catalog,MODEL

class LocalClient:
    def __init__(self,controller):self.c=controller
    def call(self,name,args=None,call_id=None):
        with self.c.lock:return self.c.catalog.dispatch(name,args or {},call_id)

class Controller:
    def __init__(self,root,state,ui,port=8765,worker=True):
        self.root,self.state,self.ui=Path(root),Path(state),Path(ui)
        self.state.mkdir(parents=True,exist_ok=True)
        self.catalog=Catalog(root,self.state/'state')
        self.lock=threading.RLock();self.token=secrets.token_urlsafe(32)
        self.port=port;self.jobs={};self.stop=threading.Event();self.refresh=threading.Event()
        self.model_state={'status':'checking','model':MODEL,'last_error':None,'working_on':None}
        config=self.state/'config.json'
        self.config=json.loads(config.read_text()) if config.exists() else {
            'timezone':'America/Chicago','gemini_model':'gemini-2.5-flash','api_key':''}
        if worker:threading.Thread(target=self.background,daemon=True).start()
    def public_config(self):
        return {'timezone':self.config['timezone'],'gemini_model':self.config['gemini_model'],
                'gemini_configured':bool(self.config.get('api_key'))}
    def status(self):
        with self.lock:
            files=self.catalog.files()['files']
            return {'name':'WhiteBoardOS','version':'0.3.0','csrf':self.token,'config':self.public_config(),
                'model':self.model_state.copy(),'files':files,'timeline':self.catalog.timeline()['events'],
                'system':self.catalog.system_status(),
                'jobs':[{k:v for k,v in job.items() if k not in ('event','decision')} for job in self.jobs.values()],
                'metadata_ready':sum(f['metadata_status']=='ready' for f in files)}
    def background(self):
        # This connection belongs exclusively to this thread. Inference holds no UI lock.
        catalog=Catalog(self.root,self.state/'state')
        last_scan=time.monotonic()
        while not self.stop.is_set():
            try:
                if self.refresh.is_set() or time.monotonic()-last_scan>60:
                    catalog.index();catalog.retry_metadata();self.refresh.clear();last_scan=time.monotonic()
                with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=3) as response:
                    tags=json.load(response)
                names={m['name'] for m in tags.get('models',[])}
                if MODEL not in names:raise ValueError('Metadata model is not installed')
                self.model_state.update(status='ready',last_error=None)
                processed=catalog.summarize_one()
                if processed:
                    self.model_state['status']='indexing'
                    self.stop.wait(0.5)
                else:self.stop.wait(10)
            except Exception as exc:
                self.model_state.update(status='unavailable',last_error=str(exc)[:160])
                self.stop.wait(10)
        catalog.db.close()
    def set_config(self,data):
        zone=data.get('timezone',self.config['timezone']);ZoneInfo(zone)
        model=data.get('gemini_model',self.config['gemini_model'])
        if not re.fullmatch(r'[A-Za-z0-9._-]+',model):raise ValueError('Invalid model ID')
        key=data.get('api_key','').strip()
        if data.get('clear_key'):self.config['api_key']=''
        elif key:self.config['api_key']=key
        self.config.update(timezone=zone,gemini_model=model)
        save(self.state/'config.json',self.config)
        os.chmod(self.state/'config.json',0o600)
        return self.public_config()
    def wifi(self):
        import subprocess
        try:
            result=subprocess.run(['/usr/bin/nmcli','-t','--escape','no','-f','SSID,SIGNAL,SECURITY',
                'device','wifi','list'],capture_output=True,text=True,timeout=15)
            if result.returncode:raise ValueError('NetworkManager is not ready')
            networks=[]
            for line in result.stdout.splitlines():
                fields=line.rsplit(':',2)
                if len(fields)==3 and fields[0]:
                    networks.append({'ssid':fields[0],'signal':fields[1],'security':fields[2]})
            return {'networks':networks}
        except (OSError,subprocess.SubprocessError,ValueError):
            return {'networks':[],'error':'Wi-Fi scan unavailable. Ethernet works without this step.'}

    def connect_wifi(self,data):
        import subprocess
        ssid=data.get('ssid','');password=data.get('password','')
        if not isinstance(ssid,str) or not ssid or len(ssid.encode())>32:raise ValueError('Invalid SSID')
        if not isinstance(password,str) or len(password)>128:raise ValueError('Invalid password')
        argv=['/usr/bin/nmcli','--wait','25','device','wifi','connect',ssid]
        if password:argv+=['password',password]
        result=subprocess.run(argv,capture_output=True,text=True,timeout=30)
        if result.returncode:raise ValueError('Connection failed. Check the network name and password.')
        return {'connected':True,'ssid':ssid}

    def power(self,data):
        import subprocess
        action=data.get('action')
        if action not in ('poweroff','reboot') or data.get('confirm')!=action:
            raise ValueError('Power action requires confirmation')
        result=subprocess.run(['/usr/bin/systemctl',action],capture_output=True,text=True,timeout=10)
        if result.returncode:raise ValueError('Power action denied by the system')
        return {'requested':action}

    def task(self,goal,task_id=None):
        if not self.config.get('api_key'):raise ValueError('Add your Gemini API key in Settings first')
        if any(j['status'] in ('running','approval') for j in self.jobs.values()):
            raise ValueError('Another task is active; finish it before starting a new one')
        if len(goal)>8000:raise ValueError('Goal is too long')
        jid=uuid.uuid4().hex
        job={'id':jid,'goal':goal,'status':'running','answer':'','pending':None,'task_id':task_id,
             'event':threading.Event(),'decision':False,'metrics':{},'cancelled':False,'error':None}
        self.jobs[jid]=job
        def approve(name,args):
            job.update(status='approval',pending={'name':name,'args':args})
            job['event'].clear()
            job['event'].wait(600)
            decision=job['decision'] and not job['cancelled']
            job.update(status='running',pending=None,decision=False)
            return decision
        def run():
            try:
                today=dt.datetime.now(ZoneInfo(self.config['timezone'])).isoformat()
                context='User timezone: '+self.config['timezone']+'; current local time: '+today+'.\n'
                runner=Runner(LocalClient(self),self.state,
                    Gemini(self.config['api_key'],self.config['gemini_model']),approve)
                result=runner.run(context+goal,task_id)
                job.update(status=result['status'],answer=result.get('answer',''),task_id=result['id'],
                    error=result.get('error'),metrics={'elapsed_seconds':result['elapsed_seconds'],
                    'tool_calls':len(result['events']),'usage':result['usage']})
            except Exception as exc:job.update(status='paused',error=str(exc))
        threading.Thread(target=run,daemon=True).start()
        return {'job_id':jid}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    @property
    def c(self):return self.server.controller
    def valid_host(self):
        return self.headers.get('Host','') in (f'127.0.0.1:{self.c.port}',f'localhost:{self.c.port}')
    def send(self,status,data,kind='application/json'):
        if isinstance(data,(dict,list)):data=json.dumps(data).encode()
        elif isinstance(data,str):data=data.encode()
        self.send_response(status)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','SAMEORIGIN')
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; object-src 'self'")
        self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if not self.valid_host():return self.send(403,{'error':'Invalid host'})
        parsed=urllib.parse.urlparse(self.path);path=parsed.path
        try:
            if path=='/api/status':return self.send(200,self.c.status())
            if path=='/api/wifi':return self.send(200,self.c.wifi())
            if path=='/api/tasks':
                values=[]
                for f in sorted((self.c.state/'tasks').glob('*.json')) if (self.c.state/'tasks').exists() else []:
                    t=json.loads(f.read_text());values.append({k:t.get(k) for k in ('id','goal','status','answer','elapsed_seconds','usage')})
                return self.send(200,{'tasks':values})
            if path.startswith('/document/'):
                fid=path.rsplit('/',1)[1]
                with self.c.lock:
                    row=self.c.catalog.row(fid);file=self.c.catalog.path(row['path'],True)
                    if file.stat().st_size>32*1024*1024:raise ValueError('File too large')
                    body=file.read_bytes()
                kind='application/pdf' if file.suffix.lower()=='.pdf' else 'text/plain; charset=utf-8'
                return self.send(200,body,kind)
            if path=='/':path='/index.html'
            target=(self.c.ui/path.lstrip('/')).resolve()
            if self.c.ui.resolve() not in target.parents or not target.is_file():
                return self.send(404,{'error':'Not found'})
            return self.send(200,target.read_bytes(),mimetypes.guess_type(target)[0] or 'application/octet-stream')
        except Exception as exc:self.send(400,{'error':str(exc)})
    def do_POST(self):
        if not self.valid_host() or self.headers.get('X-WhiteBoard-Token')!=self.c.token:
            return self.send(403,{'error':'Request denied'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=12*1024*1024:raise ValueError('Request size limit')
            data=json.loads(self.rfile.read(length))
            path=urllib.parse.urlparse(self.path).path
            with self.c.lock:
                if path=='/api/search':
                    args={k:data[k] for k in ('query','file_type','activity','time_range','limit') if k in data}
                    result=self.c.catalog.find_documents(**args,timezone=self.c.config['timezone'])
                elif path=='/api/view':result=self.c.catalog.record_view(data['file_id'])
                elif path=='/api/pin':result=self.c.catalog.pin(data['file_id'],data['pinned'])
                elif path=='/api/config':result=self.c.set_config(data)
                elif path=='/api/wifi-connect':result=self.c.connect_wifi(data)
                elif path=='/api/power':result=self.c.power(data)
                elif path=='/api/index':
                    self.c.refresh.set();result={'queued':True}
                elif path=='/api/import':
                    import base64
                    name=Path(data['name']).name
                    if name!=data['name'] or not name:raise ValueError('Invalid filename')
                    content=base64.b64decode(data['content'],validate=True)
                    if len(content)>8*1024*1024:raise ValueError('Import limit is 8 MiB')
                    if Path(name).suffix.lower() not in ('.pdf','.txt','.md','.csv','.json'):raise ValueError('Unsupported file type')
                    dst=self.c.catalog.path(name)
                    with dst.open('xb') as f:f.write(content)
                    self.c.catalog.index();self.c.refresh.set();result={'imported':name}
                elif path=='/api/task':result=self.c.task(data.get('goal',''),data.get('task_id'))
                elif path=='/api/approval':
                    job=self.c.jobs[data['job_id']]
                    if job['status']!='approval':raise ValueError('No pending approval')
                    job['decision']=bool(data['approve']);job['event'].set();result={'received':True}
                elif path=='/api/create':
                    result=self.c.catalog.write_file(data['path'],data['content'],'',uuid.uuid4().hex)
                else:return self.send(404,{'error':'Unknown endpoint'})
            self.send(200,result)
        except Exception as exc:self.send(400,{'error':str(exc)})

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',default='/var/lib/whiteboardos/documents')
    p.add_argument('--state',default='/var/lib/whiteboardos')
    p.add_argument('--ui',default='/opt/whiteboardos/ui')
    p.add_argument('--port',type=int,default=8765)
    p.add_argument('--no-worker',action='store_true')
    a=p.parse_args();os.umask(0o077)
    c=Controller(a.root,a.state,a.ui,a.port,not a.no_worker)
    with ThreadingHTTPServer(('127.0.0.1',a.port),Handler) as server:
        server.controller=c
        print('WhiteBoardOS shell ready on loopback port '+str(a.port),flush=True)
        try:server.serve_forever()
        finally:c.stop.set()

if __name__=='__main__':main()
