import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo
from .workspace import Workspace,digest,MAX_BYTES,load_bytes

MODEL='whiteboard-metadata:latest'
SCHEMA={'type':'object','properties':{
 'summary':{'type':'string'},'keywords':{'type':'array','items':{'type':'string'}},
 'project':{'type':'string'},'document_type':{'type':'string'}},
 'required':['summary','keywords','project','document_type'],'additionalProperties':False}

class Catalog(Workspace):
    def _schema(self):
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS metadata(file_id TEXT PRIMARY KEY,content_hash TEXT,summary TEXT,
          keywords TEXT,project TEXT,document_type TEXT,status TEXT,model TEXT,error TEXT);
        CREATE TABLE IF NOT EXISTS activity(id TEXT PRIMARY KEY,file_id TEXT,event TEXT,actor TEXT,timestamp REAL);
        CREATE TABLE IF NOT EXISTS relations(source TEXT,kind TEXT,target TEXT,evidence TEXT,content_hash TEXT,
          PRIMARY KEY(source,kind,target));
        CREATE TABLE IF NOT EXISTS pins(file_id TEXT PRIMARY KEY);
        ''')

    def index(self):
        self._schema()
        # Base class deletes non-text entries; save PDF IDs across its text scan.
        pdf_ids={r['path']:r['id'] for r in self.db.execute("SELECT id,path FROM files WHERE lower(path) LIKE '%.pdf'")}
        result=super().index()
        for base,dirs,names in os.walk(self.root,followlinks=False):
            dirs[:]=[x for x in dirs if not (Path(base)/x).is_symlink()]
            for name in names:
                p=Path(base)/name
                if p.suffix.lower()!='.pdf' or p.is_symlink() or not p.is_file() or p.stat().st_size>32*MAX_BYTES:
                    continue
                rel=p.relative_to(self.root).as_posix()
                try:
                    with tempfile.TemporaryFile() as out:
                        subprocess.run(['/usr/bin/pdftotext','-layout',str(p),'-'],stdout=out,
                            stderr=subprocess.DEVNULL,timeout=20,check=True)
                        out.seek(0);text=out.read(MAX_BYTES).decode('utf-8',errors='replace')
                    h=hashlib.sha256()
                    with p.open('rb') as f:
                        for block in iter(lambda:f.read(65536),b''):h.update(block)
                    fid=pdf_ids.get(rel) or uuid.uuid4().hex
                    self.db.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?)',
                        (fid,rel,h.hexdigest(),p.stat().st_size,p.stat().st_mtime_ns,' '.join(text.split())[:240]))
                    self.db.execute('DELETE FROM search WHERE id=?',(fid,))
                    self.db.execute('INSERT INTO search(id,path,body) VALUES(?,?,?)',(fid,rel,text))
                except (OSError,subprocess.SubprocessError):
                    continue
        self.db.execute('DELETE FROM metadata WHERE file_id NOT IN (SELECT id FROM files)')
        self.db.execute('DELETE FROM relations WHERE source NOT IN (SELECT id FROM files)')
        self.db.execute('DELETE FROM pins WHERE file_id NOT IN (SELECT id FROM files)')
        self.db.commit()
        result['indexed']=self.db.execute('SELECT count(*) FROM files').fetchone()[0]
        return result

    def files(self):
        rows=self.db.execute('''SELECT f.*,m.summary,m.keywords,m.project,m.document_type,
             CASE WHEN m.content_hash=f.hash THEN m.status ELSE 'pending' END AS metadata_status,
             CASE WHEN p.file_id IS NOT NULL THEN 1 ELSE 0 END AS pinned,
             (SELECT max(timestamp) FROM activity a WHERE a.file_id=f.id AND actor='human'
               AND event='opened_in_viewer') AS last_viewed
             FROM files f LEFT JOIN metadata m ON m.file_id=f.id LEFT JOIN pins p ON p.file_id=f.id
             ORDER BY f.path''')
        data=[]
        for r in rows:
            item=dict(r)
            if item['metadata_status']!='ready':
                item['summary']=item['description']
            item['keywords']=json.loads(item['keywords'] or '[]') if item['metadata_status']=='ready' else []
            data.append(item)
        return {'files':data}

    def read_file(self,file_id,offset=0,length=8192):
        row=self.row(file_id)
        if Path(row['path']).suffix.lower()!='.pdf':
            return super().read_file(file_id,offset,length)
        body=self.db.execute('SELECT body FROM search WHERE id=?',(file_id,)).fetchone()
        if not body:raise ValueError('PDF text unavailable; re-index')
        data=body['body'].encode()
        offset=max(0,int(offset));length=max(1,min(int(length),32768))
        return {'file_id':file_id,'path':row['path'],'hash':row['hash'],
            'content':data[offset:offset+length].decode(errors='replace'),'offset':offset,
            'next_offset':min(len(data),offset+length),'size':len(data),'representation':'extracted PDF text'}

    def summarize_one(self,generate=None):
        row=self.db.execute('''SELECT f.* FROM files f LEFT JOIN metadata m ON m.file_id=f.id
          WHERE m.file_id IS NULL OR m.content_hash!=f.hash OR m.status='pending'
          ORDER BY f.path LIMIT 1''').fetchone()
        if not row:return False
        row=dict(row)
        body=self.db.execute('SELECT body FROM search WHERE id=?',(row['id'],)).fetchone()['body']
        prompt='Describe this document excerpt as JSON. Filename: '+row['path']+'\n<document>\n'+body[:5000]+'\n</document>'
        try:
            if generate is None:
                request=urllib.request.Request('http://127.0.0.1:11434/api/generate',
                    data=json.dumps({'model':MODEL,'prompt':prompt,'stream':False,'format':SCHEMA,
                        'keep_alive':'30s','options':{'num_ctx':2048,'num_predict':180,'temperature':0,'num_thread':3}}).encode(),
                    headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(request,timeout=180) as response:
                    generated=json.load(response)
                value=json.loads(generated['response'])
            else:value=generate(prompt)
            if not isinstance(value,dict) or any(not isinstance(value.get(k),str) for k in ('summary','project','document_type')):
                raise ValueError('Invalid metadata fields')
            if not isinstance(value.get('keywords'),list) or any(not isinstance(x,str) for x in value['keywords']):
                raise ValueError('Invalid keywords')
            current=self.row(row['id'])
            if current['hash']!=row['hash']:return True
            self.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?,?,?,?,?,?,?,?)',
                (row['id'],row['hash'],value['summary'][:500],json.dumps(value['keywords'][:6]),
                 value['project'][:80],value['document_type'][:80],'ready',MODEL,''))
            self.db.execute("DELETE FROM relations WHERE source=? AND kind='belongs_to_project'",(row['id'],))
            project=value['project'].strip()
            # Store only project names literally supported by the indexed excerpt.
            if project and project.casefold() in body.casefold():
                self.db.execute('INSERT OR REPLACE INTO relations VALUES(?,?,?,?,?)',
                    (row['id'],'belongs_to_project',project,project,row['hash']))
        except Exception as exc:
            self.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?,?,?,?,?,?,?,?)',
                (row['id'],row['hash'],row['description'],'[]','','','error',MODEL,type(exc).__name__))
        self.db.commit();return True

    def retry_metadata(self):
        self.db.execute("UPDATE metadata SET status='pending' WHERE status='error'")
        self.db.commit();return {'queued':True}

    def record_view(self,file_id,timestamp=None):
        self.row(file_id)
        self.db.execute('INSERT INTO activity VALUES(?,?,?,?,?)',
            (uuid.uuid4().hex,file_id,'opened_in_viewer','human',timestamp or time.time()))
        self.db.commit();return {'recorded':True}

    def pin(self,file_id,pinned=True):
        self.row(file_id)
        if pinned:self.db.execute('INSERT OR IGNORE INTO pins VALUES(?)',(file_id,))
        else:self.db.execute('DELETE FROM pins WHERE file_id=?',(file_id,))
        self.db.commit();return {'pinned':bool(pinned)}

    def timeline(self,limit=30):
        rows=self.db.execute('''SELECT a.*,f.path FROM activity a LEFT JOIN files f ON a.file_id=f.id
            ORDER BY a.timestamp DESC LIMIT ?''',(max(1,min(int(limit),100)),))
        return {'events':[dict(r) for r in rows]}

    def related_files(self,file_id):
        self.row(file_id)
        rows=self.db.execute('''SELECT DISTINCT f.id,f.path,r.target AS project FROM relations r
          JOIN relations other ON other.target=r.target AND other.kind=r.kind JOIN files f ON f.id=other.source
          JOIN files source ON source.id=r.source
          WHERE r.source=? AND r.kind='belongs_to_project' AND r.content_hash=source.hash
          AND other.content_hash=f.hash AND f.id!=?''',(file_id,file_id))
        return {'related':[dict(r) for r in rows]}

    def find_documents(self,query,file_type='',activity='',time_range='',timezone='America/Chicago',limit=5,now=None):
        if activity not in ('','human_view'):raise ValueError('Unsupported activity')
        if time_range not in ('','today','yesterday','last_week'):raise ValueError('Unsupported time range')
        zone=ZoneInfo(timezone)
        current=dt.datetime.fromtimestamp(now or time.time(),zone)
        start=end=None
        if time_range:
            day=current.date()
            if time_range=='yesterday':day-=dt.timedelta(days=1)
            start=dt.datetime.combine(day,dt.time.min,zone)
            if time_range=='last_week':start-=dt.timedelta(days=6)
            end=dt.datetime.combine(day+dt.timedelta(days=1),dt.time.min,zone)
        terms=set(re.findall(r'\w+',query.casefold()))
        candidates=[]
        for f in self.files()['files']:
            if file_type and Path(f['path']).suffix.lower().lstrip('.')!=file_type.lower():continue
            last=f['last_viewed']
            if activity and not last:continue
            if start:
                event=self.db.execute('''SELECT max(timestamp) FROM activity WHERE file_id=? AND actor='human'
                  AND event='opened_in_viewer' AND timestamp>=? AND timestamp<?''',
                    (f['id'],start.timestamp(),end.timestamp())).fetchone()[0]
                if event is None:continue
                last=event
            body=self.db.execute('SELECT body FROM search WHERE id=?',(f['id'],)).fetchone()
            haystack=' '.join([f['path'],f['summary'] or '', ' '.join(f['keywords']),body['body'] if body else '']).casefold()
            score=sum(1 for term in terms if term in haystack)
            if terms and not score:continue
            candidates.append({'file_id':f['id'],'path':f['path'],'summary':f['summary'],
                'score':score,'viewed_at':dt.datetime.fromtimestamp(last,zone).isoformat() if last else None,
                'metadata_source':'local_llm' if f['metadata_status']=='ready' else 'text_excerpt'})
        candidates.sort(key=lambda x:(x['score'],x['viewed_at'] or ''),reverse=True)
        return {'matches':candidates[:max(1,min(int(limit),20))],
                'time_window':{'start':start.isoformat(),'end':end.isoformat()} if start else None,
                'method':'keyword matching over text and local model metadata, with activity filtering'}

    def dispatch(self,name,args,call_id=None):
        own={'find_documents','related_files','timeline'}
        if name in own:return getattr(self,name)(**args)
        return super().dispatch(name,args,call_id)
