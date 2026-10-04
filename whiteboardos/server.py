"""Loopback WhiteBoardOS desktop backend.

The public UI intentionally exposes no cloud-agent API. Local semantic metadata remains
provided by the catalog/Ollama worker. The browser terminal is backed by a real pseudo-terminal session running /bin/sh as the same unprivileged service account as WhiteBoardOS.
"""
import argparse
import fcntl
import json
import mimetypes
import os
import pty
import re
import secrets
import select
import signal
import struct
import subprocess
import termios
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
from .catalog import Catalog,MODEL


class PTYSession:
    def __init__(self,root,cols=88,rows=25):
        self.root=Path(root).resolve()
        self.id=secrets.token_urlsafe(18)
        self.lock=threading.RLock()
        self.last_used=time.monotonic()
        master,slave=pty.openpty()
        self.master=master
        self._set_size(cols,rows)
        env=os.environ.copy()
        env.update({
            'PATH':'/usr/local/bin:/usr/bin:/bin',
            'HOME':str(self.root.parent),
            'USER':'whiteboard',
            'LOGNAME':'whiteboard',
            'LANG':'C.UTF-8',
            'TERM':'xterm-256color',
            'COLORTERM':'truecolor',
            'PS1':'whiteboard@os:$PWD $ ',
        })
        # Do session/controlling-TTY setup in a tiny child Python helper rather than
        # subprocess.preexec_fn; the HTTP server is multi-threaded and preexec_fn is unsafe there.
        helper=("import os,fcntl,termios;"
                "os.setsid();fcntl.ioctl(0,termios.TIOCSCTTY,0);"
                "os.execvpe('/bin/sh',['/bin/sh','-i'],os.environ)")
        self.proc=subprocess.Popen(['/usr/bin/python3','-c',helper],stdin=slave,stdout=slave,stderr=slave,
            cwd=self.root,env=env,close_fds=True)
        os.close(slave)
        os.set_blocking(master,False)

    def _set_size(self,cols,rows):
        cols=max(20,min(int(cols),240));rows=max(6,min(int(rows),100))
        fcntl.ioctl(self.master,termios.TIOCSWINSZ,struct.pack('HHHH',rows,cols,0,0))

    def resize(self,cols,rows):
        with self.lock:
            self.last_used=time.monotonic();self._set_size(cols,rows)
        return {'ok':True}

    def write(self,data):
        if not isinstance(data,str) or len(data.encode('utf-8'))>16384:raise ValueError('Terminal input too large')
        payload=data.encode('utf-8')
        with self.lock:
            self.last_used=time.monotonic()
            view=memoryview(payload)
            while view:
                try:n=os.write(self.master,view)
                except BlockingIOError:time.sleep(.005);continue
                view=view[n:]
        return {'ok':True}

    def read(self):
        chunks=[];total=0
        with self.lock:
            self.last_used=time.monotonic()
            while total<65536:
                try:
                    ready,_,_=select.select([self.master],[],[],0)
                    if not ready:break
                    data=os.read(self.master,min(8192,65536-total))
                    if not data:break
                    chunks.append(data);total+=len(data)
                except (BlockingIOError,OSError):break
            closed=self.proc.poll() is not None
        return {'data':b''.join(chunks).decode('utf-8','replace'),'closed':closed,'code':self.proc.returncode if closed else None}

    def close(self):
        with self.lock:
            if self.proc.poll() is None:
                try:os.killpg(self.proc.pid,signal.SIGHUP)
                except OSError:pass
                try:self.proc.wait(timeout=.5)
                except subprocess.TimeoutExpired:
                    try:os.killpg(self.proc.pid,signal.SIGKILL)
                    except OSError:pass
            try:os.close(self.master)
            except OSError:pass

class Controller:
    def __init__(self,root,state,ui,port=8765,worker=True):
        self.root,self.state,self.ui=Path(root),Path(state),Path(ui)
        self.root.mkdir(parents=True,exist_ok=True)
        self.state.mkdir(parents=True,exist_ok=True)
        self.catalog=Catalog(root,self.state/'state')
        self.lock=threading.RLock();self.token=secrets.token_urlsafe(32)
        self.term_lock=threading.RLock();self.terminals={}
        self.port=port;self.stop=threading.Event();self.refresh=threading.Event()
        self.model_state={'status':'checking','model':MODEL,'last_error':None,'working_on':None}
        config=self.state/'config.json'
        previous=json.loads(config.read_text()) if config.exists() else {}
        # Cloud-agent settings are deliberately discarded. WhiteBoardOS desktop only keeps
        # local OS preferences; this also removes an old Gemini key from persistent state.
        self.config={'timezone':previous.get('timezone','America/Chicago')}
        ZoneInfo(self.config['timezone'])
        self._save_config()
        if worker:threading.Thread(target=self.background,daemon=True).start()
        threading.Thread(target=self.embedding_keepalive,daemon=True).start()

    def _save_config(self):
        path=self.state/'config.json'
        temp=self.state/'config.tmp'
        temp.write_text(json.dumps(self.config,indent=2),encoding='utf-8')
        os.replace(temp,path);os.chmod(path,0o600)

    def public_config(self):
        return {'timezone':self.config['timezone']}

    def status(self):
        with self.lock:
            files=self.catalog.files()['files']
            return {'name':'WhiteBoardOS','version':'0.12-action-router-v2.7','csrf':self.token,
                'config':self.public_config(),'model':self.model_state.copy(),'files':files,
                'timeline':self.catalog.timeline()['events'],'system':self.catalog.system_status(),
                'folders':self.catalog.folders(),'trash':self.catalog.trash_items(),
                'metadata_ready':sum(f['metadata_status']=='ready' for f in files),
                'retrieval':self.catalog.retrieval_status()}

    def background(self):
        catalog=Catalog(self.root,self.state/'state')
        # Poll a cheap path/size/mtime signature so terminal editors and other local
        # programs receive the same indexing guarantees as GUI/API mutations.
        last_signature=catalog.filesystem_signature()
        last_scan=time.monotonic()
        while not self.stop.is_set():
            try:
                current_signature=catalog.filesystem_signature()
                changed=current_signature!=last_signature
                if self.refresh.is_set() or changed or time.monotonic()-last_scan>30:
                    catalog.index();catalog.retry_metadata();self.refresh.clear();last_scan=time.monotonic()
                    last_signature=catalog.filesystem_signature()
                with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=3) as response:
                    tags=json.load(response)
                names={m['name'] for m in tags.get('models',[])}
                if MODEL not in names:raise ValueError('Metadata model is not installed')
                self.model_state.update(status='ready',last_error=None)
                processed=catalog.summarize_one()
                # Finish the cheap Qwen metadata pass first. With OLLAMA_MAX_LOADED_MODELS=1,
                # alternating Qwen and the embedding model per file would repeatedly swap models.
                # Only enter the embedding phase once no metadata item is pending.
                if processed:
                    self.model_state['status']='indexing';self.stop.wait(0.5)
                else:
                    retrieval=catalog.retrieval().refresh(limit=8,rebuild=True)
                    if retrieval['indexed']:
                        self.model_state['status']='indexing';self.stop.wait(0.5)
                    else:self.stop.wait(2)
            except Exception as exc:
                self.model_state.update(status='unavailable',last_error=str(exc)[:160]);self.stop.wait(10)
        catalog.db.close()

    def embedding_keepalive(self):
        """Warm the embedding model after boot and periodically keep it resident."""
        # Ollama and model-import can finish after this service starts. Retry quietly.
        for _ in range(30):
            if self.stop.is_set():
                return
            try:
                with self.lock:
                    result=self.catalog.warm_retrieval()
                if result.get('ok'):
                    break
            except Exception:
                pass
            self.stop.wait(2)
        # OLLAMA_KEEP_ALIVE=-1 should keep the model resident. This periodic no-op
        # warm is a second line of defense for older runtimes/configurations.
        while not self.stop.wait(240):
            try:
                with self.lock:
                    self.catalog.warm_retrieval()
            except Exception:
                pass

    def set_config(self,data):
        zone=data.get('timezone',self.config['timezone'])
        ZoneInfo(zone);self.config={'timezone':zone};self._save_config();return self.public_config()

    def wifi(self):
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
        ssid=data.get('ssid','');password=data.get('password','')
        if not isinstance(ssid,str) or not ssid or len(ssid.encode())>32:raise ValueError('Invalid SSID')
        if not isinstance(password,str) or len(password)>128:raise ValueError('Invalid password')
        argv=['/usr/bin/nmcli','--wait','25','device','wifi','connect',ssid]
        if password:argv+=['password',password]
        result=subprocess.run(argv,capture_output=True,text=True,timeout=30)
        if result.returncode:raise ValueError('Connection failed. Check the network name and password.')
        return {'connected':True,'ssid':ssid}

    def power(self,data):
        action=data.get('action')
        if action not in ('poweroff','reboot') or data.get('confirm')!=action:
            raise ValueError('Power action requires confirmation')
        result=subprocess.run(['/usr/bin/systemctl',action],capture_output=True,text=True,timeout=10)
        if result.returncode:raise ValueError('Power action denied by the system')
        return {'requested':action}

    def _terminal_session(self,session_id):
        if not isinstance(session_id,str) or len(session_id)>128:raise ValueError('Invalid terminal session')
        with self.term_lock:
            session=self.terminals.get(session_id)
        if not session:raise ValueError('Terminal session expired')
        return session

    def terminal_open(self,data):
        cols=data.get('cols',88);rows=data.get('rows',25)
        session=PTYSession(self.root,cols,rows)
        with self.term_lock:
            stale=[sid for sid,item in self.terminals.items() if time.monotonic()-item.last_used>1800 or item.proc.poll() is not None]
            for sid in stale:
                try:self.terminals.pop(sid).close()
                except Exception:pass
            if len(self.terminals)>=6:
                oldest=min(self.terminals.values(),key=lambda item:item.last_used)
                self.terminals.pop(oldest.id,None);oldest.close()
            self.terminals[session.id]=session
        return {'session':session.id,'cols':int(cols),'rows':int(rows)}

    def terminal_read(self,data):
        return self._terminal_session(data.get('session')).read()

    def terminal_write(self,data):
        return self._terminal_session(data.get('session')).write(data.get('data',''))

    def terminal_resize(self,data):
        return self._terminal_session(data.get('session')).resize(data.get('cols',88),data.get('rows',25))

    def terminal_close(self,data):
        sid=data.get('session')
        with self.term_lock:session=self.terminals.pop(sid,None)
        if session:session.close()
        return {'closed':True}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    @property
    def c(self):return self.server.controller
    def valid_host(self):
        return self.headers.get('Host','') in (f'127.0.0.1:{self.c.port}',f'localhost:{self.c.port}')
    def send(self,status,data,kind='application/json'):
        if isinstance(data,(dict,list)):data=json.dumps(data).encode()
        elif isinstance(data,str):data=data.encode()
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','SAMEORIGIN')
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; object-src 'self'")
        self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if not self.valid_host():return self.send(403,{'error':'Invalid host'})
        parsed=urllib.parse.urlparse(self.path);path=parsed.path
        try:
            if path=='/api/status':return self.send(200,self.c.status())
            if path=='/api/wifi':return self.send(200,self.c.wifi())
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
            if self.c.ui.resolve() not in target.parents or not target.is_file():return self.send(404,{'error':'Not found'})
            return self.send(200,target.read_bytes(),mimetypes.guess_type(target)[0] or 'application/octet-stream')
        except Exception as exc:self.send(400,{'error':str(exc)})
    def do_POST(self):
        if not self.valid_host() or self.headers.get('X-WhiteBoard-Token')!=self.c.token:
            return self.send(403,{'error':'Request denied'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=12*1024*1024:raise ValueError('Request size limit')
            data=json.loads(self.rfile.read(length));path=urllib.parse.urlparse(self.path).path
            with self.c.lock:
                if path=='/api/search':
                    args={k:data[k] for k in ('query','file_type','activity','time_range','limit') if k in data}
                    result=self.c.catalog.find_documents(**args,timezone=self.c.config['timezone'])
                elif path=='/api/retrieve':
                    result=self.c.catalog.retrieve(data['query'],limit=data.get('limit',0),
                        debug=bool(data.get('debug',False)),include_excerpts=bool(data.get('include_excerpts',False)),
                        max_chars=data.get('max_chars',12000))
                elif path=='/api/retrieval-rebuild':
                    result=self.c.catalog.rebuild_retrieval(limit=data.get('limit',200),force=bool(data.get('force',False)))
                elif path=='/api/retrieval-warm':
                    result=self.c.catalog.warm_retrieval()
                elif path=='/api/sync-status':
                    result=self.c.catalog.sync_status(data.get('limit',20))
                elif path=='/api/view':result=self.c.catalog.record_view(data['file_id'])
                elif path=='/api/pin':result=self.c.catalog.pin(data['file_id'],data['pinned'])
                elif path=='/api/delete':result=self.c.catalog.delete_file(data['file_id']);self.c.refresh.set()
                elif path=='/api/rename':result=self.c.catalog.rename_file(data['file_id'],data['new_name']);self.c.refresh.set()
                elif path=='/api/move':result=self.c.catalog.move_to_folder(data['file_id'],data.get('folder',''));self.c.refresh.set()
                elif path=='/api/batch-move':result=self.c.catalog.move_many(data.get('moves',[]));self.c.refresh.set()
                elif path=='/api/consolidate':result=self.c.catalog.consolidate_files(data.get('file_ids',[]),data.get('destination',''));self.c.refresh.set()
                elif path=='/api/duplicate':result=self.c.catalog.duplicate_file(data['file_id']);self.c.refresh.set()
                elif path=='/api/folder':result=self.c.catalog.create_folder(data['path'])
                elif path=='/api/shortcut':result=self.c.catalog.shortcut(data['file_id'],data.get('enabled',True))
                elif path=='/api/restore':result=self.c.catalog.restore_trash(data['recovery_id']);self.c.refresh.set()
                elif path=='/api/task':raise ValueError('Gemini agent is disabled in Settings; use local search or the external benchmark script.')
                elif path=='/api/config':result=self.c.set_config(data)
                elif path=='/api/wifi-connect':result=self.c.connect_wifi(data)
                elif path=='/api/power':result=self.c.power(data)
                elif path=='/api/terminal-open':result=self.c.terminal_open(data)
                elif path=='/api/terminal-read':result=self.c.terminal_read(data)
                elif path=='/api/terminal-write':result=self.c.terminal_write(data)
                elif path=='/api/terminal-resize':result=self.c.terminal_resize(data)
                elif path=='/api/terminal-close':result=self.c.terminal_close(data)
                elif path=='/api/index':self.c.refresh.set();result={'queued':True}
                elif path=='/api/import':
                    import base64
                    name=Path(data['name']).name
                    if name!=data['name'] or not name:raise ValueError('Invalid filename')
                    content=base64.b64decode(data['content'],validate=True)
                    if len(content)>8*1024*1024:raise ValueError('Import limit is 8 MiB')
                    if Path(name).suffix.lower() not in ('.pdf','.txt','.md','.csv','.json','.py'):raise ValueError('Unsupported file type')
                    folder=(data.get('folder') or '').strip().strip('/')
                    rel=(Path(folder)/name).as_posix() if folder else name
                    dst=self.c.catalog.path(rel)
                    if folder and not dst.parent.is_dir():raise ValueError('Destination folder does not exist')
                    with dst.open('xb') as f:f.write(content)
                    self.c.catalog.index();row=self.c.catalog.db.execute('SELECT id FROM files WHERE path=?',(rel,)).fetchone();self.c.catalog._sync_content_mutation('import',row['id'] if row else None,'',rel);self.c.refresh.set();result={'imported':rel}
                elif path=='/api/create':result=self.c.catalog.write_file(data['path'],data['content'],'',uuid.uuid4().hex);self.c.refresh.set()
                else:return self.send(404,{'error':'Unknown endpoint'})
            self.send(200,result)
        except Exception as exc:self.send(400,{'error':str(exc)})

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',default='/var/lib/whiteboardos/documents')
    p.add_argument('--state',default='/var/lib/whiteboardos');p.add_argument('--ui',default='/opt/whiteboardos/ui')
    p.add_argument('--port',type=int,default=8765);p.add_argument('--no-worker',action='store_true')
    a=p.parse_args();os.umask(0o077);c=Controller(a.root,a.state,a.ui,a.port,not a.no_worker)
    with ThreadingHTTPServer(('127.0.0.1',a.port),Handler) as server:
        server.controller=c;print('WhiteBoardOS desktop ready on loopback port '+str(a.port),flush=True)
        try:server.serve_forever()
        finally:
            c.stop.set()
            with c.term_lock:
                sessions=list(c.terminals.values());c.terminals.clear()
            for session in sessions:
                try:session.close()
                except Exception:pass

if __name__=='__main__':main()
