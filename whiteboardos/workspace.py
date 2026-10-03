"""Serialized workspace engine; all content access stays in one private tree."""
import hashlib
import json
import os
import re
import sqlite3
import stat
import time
import uuid
from pathlib import Path

MAX_BYTES = 1024 * 1024

def digest(data):
    return hashlib.sha256(data).hexdigest()

def load_bytes(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size>MAX_BYTES:
            raise ValueError('Only regular files up to 1 MiB are supported')
        with os.fdopen(fd,'rb',closefd=False) as f:
            data=f.read(MAX_BYTES+1)
        if len(data)>MAX_BYTES:
            raise ValueError('File grew beyond size limit')
        return data
    finally:
        os.close(fd)

def digest_file(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size>32*MAX_BYTES:
            raise ValueError('Unsupported file size or type')
        h=hashlib.sha256()
        with os.fdopen(fd,'rb',closefd=False) as f:
            for block in iter(lambda:f.read(65536),b''):h.update(block)
        return h.hexdigest()
    finally:os.close(fd)

def sync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)

class Workspace:
    def __init__(self, root, state):
        self.root = Path(root).resolve()
        self.state = Path(state).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(parents=True, exist_ok=True)
        if self.state == self.root or self.root in self.state.parents:
            raise ValueError('State must be outside document tree')
        self.db = sqlite3.connect(self.state / 'workspace.sqlite',check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,path TEXT UNIQUE,hash TEXT,size INTEGER,mtime INTEGER,description TEXT);
        CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(id UNINDEXED,path,body);
        CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,call_id TEXT UNIQUE,kind TEXT,status TEXT,payload TEXT,result TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS memo(call_id TEXT PRIMARY KEY,args TEXT,result TEXT);
        ''')
        self.recover()
        self.index()

    def path(self, relative, existing=False):
        if not isinstance(relative, str) or not relative or '\x00' in relative:
            raise ValueError('Invalid relative path')
        p = Path(relative)
        if p.is_absolute() or any(x in ('..', '.') for x in p.parts):
            raise ValueError('Path must stay within workspace')
        current = self.root
        for part in p.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError('Symlinks are unsupported')
        resolved = current.resolve()
        if self.root not in resolved.parents:
            raise ValueError('Path escapes workspace')
        if existing and not current.is_file():
            raise ValueError('File does not exist')
        return current

    def row(self, file_id):
        row = self.db.execute('SELECT * FROM files WHERE id=?', (file_id,)).fetchone()
        if not row:
            raise ValueError('Unknown file ID; search again')
        return dict(row)

    def index(self):
        start = time.monotonic()
        seen = set()
        for base, dirs, names in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(x for x in dirs if not (Path(base)/x).is_symlink())
            for name in sorted(names):
                p = Path(base)/name
                if p.is_symlink() or not p.is_file() or p.suffix.lower() not in ('.txt', '.md', '.csv', '.json'):
                    continue
                rel = p.relative_to(self.root).as_posix()
                data = load_bytes(p) if p.stat().st_size <= MAX_BYTES else None
                if data is None:
                    continue
                try:
                    text = data.decode('utf-8')
                except UnicodeDecodeError:
                    continue
                h = digest(data)
                old = self.db.execute('SELECT * FROM files WHERE path=?', (rel,)).fetchone()
                fid = old['id'] if old else uuid.uuid4().hex
                seen.add(fid)
                if old and old['hash'] == h:
                    continue
                self.db.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?)',
                    (fid, rel, h, len(data), p.stat().st_mtime_ns, ' '.join(text.split())[:240]))
                self.db.execute('DELETE FROM search WHERE id=?', (fid,))
                self.db.execute('INSERT INTO search(id,path,body) VALUES(?,?,?)', (fid, rel, text))
        for r in self.db.execute('SELECT id FROM files').fetchall():
            if r['id'] not in seen:
                self.db.execute('DELETE FROM files WHERE id=?', (r['id'],))
                self.db.execute('DELETE FROM search WHERE id=?', (r['id'],))
        self.db.commit()
        catalog = json.dumps(self.list_files()['files'], ensure_ascii=False, indent=2)
        temp = self.state / 'catalog.tmp'
        temp.write_text(catalog, encoding='utf-8')
        os.replace(temp, self.state / 'catalog.json')
        return {'indexed': len(seen), 'elapsed_ms': round((time.monotonic()-start)*1000, 2)}

    def list_files(self, limit=200):
        limit = max(1, min(int(limit), 200))
        rows = [dict(r) for r in self.db.execute('SELECT * FROM files ORDER BY path LIMIT ?', (limit,))]
        return {'files': rows, 'total': self.db.execute('SELECT count(*) FROM files').fetchone()[0]}

    def search_files(self, query, limit=5):
        terms = re.findall(r'\w+', query, re.UNICODE)[:20]
        if not terms:
            return {'matches': []}
        expression = ' OR '.join('"'+t.replace('"','""')+'"' for t in terms)
        rows = self.db.execute('''SELECT files.*,snippet(search,2,'[',']','...',40) AS excerpt
            FROM search JOIN files ON files.id=search.id WHERE search MATCH ?
            ORDER BY bm25(search) LIMIT ?''', (expression, max(1,min(int(limit),20))))
        return {'matches': [dict(r) for r in rows]}

    def read_file(self, file_id, offset=0, length=8192):
        row = self.row(file_id)
        data = load_bytes(self.path(row['path'], True))
        if len(data) > MAX_BYTES:
            raise ValueError('File exceeds size limit')
        offset, length = max(0,int(offset)), max(1,min(int(length),32768))
        return {'file_id': file_id, 'path': row['path'], 'hash': digest(data),
                'content': data[offset:offset+length].decode('utf-8',errors='replace'),
                'offset': offset, 'next_offset': min(len(data),offset+length), 'size':len(data)}

    def _record(self, call_id, kind, payload):
        oid = uuid.uuid4().hex
        self.db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?)',
            (oid,call_id,kind,'pending',json.dumps(payload),None,time.time()))
        self.db.commit()
        return oid

    def _finish(self, oid, result):
        self.db.execute('UPDATE operations SET status=?,result=? WHERE id=?', ('done',json.dumps(result),oid))
        self.db.commit()
        return result

    def move_file(self, file_id, destination, expected_hash, call_id):
        row = self.row(file_id)
        src, dst = self.path(row['path'], True), self.path(destination)
        if digest_file(src) != expected_hash:
            raise ValueError('Source changed; read it again')
        if dst.exists():
            raise ValueError('Destination exists; no overwrites')
        oid = self._record(call_id,'move',{'file_id':file_id,'source':row['path'],
            'destination':destination,'hash':expected_hash})
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Hard-link then unlink prevents accidental destination overwrite.
        os.link(src,dst,follow_symlinks=False)
        src.unlink()
        sync_dir(dst.parent)
        sync_dir(src.parent)
        self.db.execute('UPDATE files SET path=? WHERE id=?',(destination,file_id))
        self.db.execute('UPDATE search SET path=? WHERE id=?',(destination,file_id))
        self.db.commit()
        return self._finish(oid, {'operation_id':oid,'file_id':file_id,'path':destination})

    def write_file(self, path, content, expected_hash, call_id):
        data = content.encode('utf-8')
        if len(data)>MAX_BYTES or Path(path).suffix.lower() not in ('.txt','.md','.csv','.json'):
            raise ValueError('Unsupported file type or size')
        dst = self.path(path)
        existed = dst.exists()
        before = load_bytes(dst) if existed else b''
        if existed and digest(before)!=expected_hash:
            raise ValueError('Expected hash required and must match current content')
        if not existed and expected_hash:
            raise ValueError('Cannot update missing file')
        oid = uuid.uuid4().hex
        backup = self.state/(oid+'.before')
        backup.write_bytes(before)
        with backup.open('rb') as handle:
            os.fsync(handle.fileno())
        sync_dir(self.state)
        oid_db = self._record(call_id,'write',{'path':path,'existed':existed,
            'before_hash':digest(before),'after_hash':digest(data),'backup':backup.name})
        dst.parent.mkdir(parents=True,exist_ok=True)
        temp = dst.parent/('.agentos-'+oid_db+'.tmp')
        with temp.open('xb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp,dst)
        sync_dir(dst.parent)
        self.index()
        return self._finish(oid_db,{'operation_id':oid_db,'path':path,'hash':digest(data)})

    def recover(self):
        for r in self.db.execute("SELECT * FROM operations WHERE status='pending'").fetchall():
            p = json.loads(r['payload'])
            result = None
            if r['kind']=='move':
                src,dst = self.path(p['source']),self.path(p['destination'])
                if dst.is_file() and digest_file(dst)==p['hash']:
                    if src.is_file() and os.path.samefile(src,dst):
                        src.unlink()
                    if not src.exists():
                        self.db.execute('UPDATE files SET path=? WHERE id=?',(p['destination'],p['file_id']))
                        self.db.execute('UPDATE search SET path=? WHERE id=?',(p['destination'],p['file_id']))
                        result={'operation_id':r['id'],'file_id':p['file_id'],'path':p['destination']}
            elif r['kind']=='write':
                dst=self.path(p['path'])
                if dst.is_file() and digest(load_bytes(dst))==p['after_hash']:
                    result={'operation_id':r['id'],'path':p['path'],'hash':p['after_hash']}
            if result:
                self._finish(r['id'],result)
            else:
                self.db.execute("UPDATE operations SET status='interrupted' WHERE id=?",(r['id'],))
                self.db.commit()

    def undo(self, operation_id, call_id):
        r=self.db.execute('SELECT * FROM operations WHERE id=?',(operation_id,)).fetchone()
        if r and r['status']=='undone':
            return {'undone':operation_id}
        if not r or r['status']!='done':
            raise ValueError('Operation is not undoable')
        p=json.loads(r['payload'])
        if r['kind']=='move':
            self.move_file(p['file_id'],p['source'],p['hash'],call_id+':inverse')
        elif r['kind']=='write':
            dst=self.path(p['path'],True)
            if digest(load_bytes(dst))!=p['after_hash']:
                raise ValueError('File changed since operation; undo refused')
            if not p['existed']:
                raise ValueError('Undo of new-file creation is not supported; original remains intact')
            before=(self.state/p['backup']).read_bytes().decode('utf-8')
            self.write_file(p['path'],before,p['after_hash'],call_id+':inverse')
        self.db.execute("UPDATE operations SET status='undone' WHERE id=?",(operation_id,))
        self.db.commit()
        self.index()
        return {'undone':operation_id}

    def history(self, limit=20):
        return {'operations':[dict(r) for r in self.db.execute(
            'SELECT id,kind,status,result,created FROM operations ORDER BY created DESC LIMIT ?',
            (max(1,min(int(limit),100)),))]}

    def system_status(self):
        import shutil
        usage=shutil.disk_usage(self.root)
        return {'platform':os.uname().sysname,'architecture':os.uname().machine,
            'load_average':list(os.getloadavg()),'workspace_free_bytes':usage.free,
            'files':self.list_files(1)['total']}

    def run_program(self, program):
        import subprocess
        import shutil
        commands={'system_info':['uname','-a'],
                  'disk_usage':['df','-h',str(self.root)],
                  'memory_usage':['free','-m']}
        if program not in commands:
            raise ValueError('Program is not registered')
        argv=commands[program].copy()
        executable=shutil.which(argv[0],path='/usr/bin:/bin:/usr/sbin:/sbin')
        if not executable:raise ValueError('Registered program is not installed')
        argv[0]=executable
        result=subprocess.run(argv,capture_output=True,text=True,timeout=5,
            env={'PATH':'/usr/bin:/bin','LANG':'C'},cwd=self.root,shell=False)
        return {'program':program,'exit_code':result.returncode,
                'stdout':result.stdout[:8192],'stderr':result.stderr[:2048]}

    def dispatch(self, name, args, call_id=None):
        allowed={'search_files','read_file','list_files','index','move_file','write_file','undo','history','system_status','run_program'}
        if name not in allowed:
            raise ValueError('Unknown tool')
        args=dict(args)
        if 'call_id' in args:
            raise ValueError('call_id is runtime-owned')
        signature=json.dumps({'name':name,'args':args},sort_keys=True)
        if call_id:
            memo=self.db.execute('SELECT * FROM memo WHERE call_id=?',(call_id,)).fetchone()
            if memo:
                if memo['args']!=signature:
                    raise ValueError('Call ID reused with different arguments')
                return json.loads(memo['result'])
            op=self.db.execute('SELECT * FROM operations WHERE call_id=?',(call_id,)).fetchone()
            if op:
                if op['status']=='done':
                    return json.loads(op['result'])
                raise ValueError('Previous call interrupted; inspect history before retrying')
        if name in ('move_file','write_file','undo'):
            args['call_id']=call_id or uuid.uuid4().hex
        result=getattr(self,name)(**args)
        if call_id:
            self.db.execute('INSERT INTO memo VALUES(?,?,?)',(call_id,signature,json.dumps(result)))
            self.db.commit()
        return result
