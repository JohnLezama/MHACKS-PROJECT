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
from .workspace import Workspace,digest,MAX_BYTES,load_bytes,sync_dir
from .retrieval import RetrievalService

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
        CREATE TABLE IF NOT EXISTS shortcuts(file_id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS mutation_log(
          id TEXT PRIMARY KEY,kind TEXT,file_id TEXT,path_before TEXT,path_after TEXT,
          content_changed INTEGER,graph_updated INTEGER,created REAL,detail TEXT);
        ''')
        self.retrieval().ensure_schema()

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
        self.db.execute('DELETE FROM shortcuts WHERE file_id NOT IN (SELECT id FROM files)')
        self.retrieval().cleanup()
        self.db.commit()
        result['indexed']=self.db.execute('SELECT count(*) FROM files').fetchone()[0]
        return result

    def _snapshot_catalog(self):
        """Write the human/debug JSON catalog from current SQLite state."""
        payload=json.dumps(self.files()['files'],ensure_ascii=False,indent=2)
        temp=self.state/'catalog.tmp'
        temp.write_text(payload,encoding='utf-8')
        os.replace(temp,self.state/'catalog.json')

    def _log_mutation(self,kind,file_id=None,path_before='',path_after='',content_changed=False,graph_updated=False,detail=''):
        self.db.execute('INSERT INTO mutation_log VALUES(?,?,?,?,?,?,?,?,?)',
            (uuid.uuid4().hex,kind,file_id,path_before or '',path_after or '',1 if content_changed else 0,
             1 if graph_updated else 0,time.time(),detail[:500]))
        self.db.commit()

    def _sync_path_mutation(self,kind,file_id,path_before,path_after):
        """Path-only mutation: SQLite paths are already updated; keep embedding, rebuild sparse graph."""
        self.retrieval().cleanup()
        graph=self.retrieval().rebuild_graph()
        self._snapshot_catalog()
        self._log_mutation(kind,file_id,path_before,path_after,False,True,json.dumps(graph,separators=(',',':')))
        return graph

    def _sync_content_mutation(self,kind,file_id=None,path_before='',path_after=''):
        """Content mutation: catalog immediately current; metadata/vector are deliberately invalidated.

        The background worker regenerates Qwen metadata first and then the semantic vector.
        Stale graph edges are removed immediately, so retrieval never uses old content.
        """
        if file_id:
            self.db.execute('DELETE FROM embeddings WHERE file_id=?',(file_id,))
            self.db.execute('DELETE FROM file_relations WHERE source=? OR target=?',(file_id,file_id))
        self.retrieval().cleanup()
        self.db.commit()
        self._snapshot_catalog()
        self._log_mutation(kind,file_id,path_before,path_after,True,True,'stale semantic state invalidated; background refresh queued')

    def sync_status(self,limit=20):
        events=[dict(r) for r in self.db.execute('SELECT * FROM mutation_log ORDER BY created DESC LIMIT ?',
            (max(1,min(int(limit),100)),))]
        return {'retrieval':self.retrieval_status(),'recent_mutations':events}

    def filesystem_signature(self):
        """Cheap polling signature used to notice terminal/editor changes outside the API."""
        h=hashlib.blake2b(digest_size=16)
        count=0
        for base,dirs,names in os.walk(self.root,followlinks=False):
            dirs[:]=sorted(x for x in dirs if not (Path(base)/x).is_symlink() and not x.startswith('.'))
            for name in sorted(names):
                p=Path(base)/name
                if p.is_symlink() or not p.is_file() or p.suffix.lower() not in ('.txt','.md','.csv','.json','.py','.pdf'):
                    continue
                try:st=p.stat()
                except OSError:continue
                rel=p.relative_to(self.root).as_posix()
                h.update(rel.encode());h.update(str(st.st_size).encode());h.update(str(st.st_mtime_ns).encode());count+=1
        return count,h.hexdigest()

    def write_file(self,path,content,expected_hash,call_id):
        before=self.db.execute('SELECT id,path FROM files WHERE path=?',(path,)).fetchone()
        result=super().write_file(path,content,expected_hash,call_id)
        row=self.db.execute('SELECT id,path FROM files WHERE path=?',(path,)).fetchone()
        file_id=row['id'] if row else (before['id'] if before else None)
        self._sync_content_mutation('write',file_id,before['path'] if before else '',path)
        return result

    def files(self):
        rows=self.db.execute('''SELECT f.*,m.summary,m.keywords,m.project,m.document_type,
             CASE WHEN m.content_hash=f.hash THEN m.status ELSE 'pending' END AS metadata_status,
             CASE WHEN p.file_id IS NOT NULL THEN 1 ELSE 0 END AS pinned,
             CASE WHEN s.file_id IS NOT NULL THEN 1 ELSE 0 END AS shortcut,
             (SELECT max(timestamp) FROM activity a WHERE a.file_id=f.id AND actor='human'
               AND event='opened_in_viewer') AS last_viewed
             FROM files f LEFT JOIN metadata m ON m.file_id=f.id LEFT JOIN pins p ON p.file_id=f.id
             LEFT JOIN shortcuts s ON s.file_id=f.id
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
        self.db.commit()
        return True

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

    def delete_file(self,file_id):
        """Move one workspace file into private recoverable trash, then re-index."""
        row=self.row(file_id)
        src=self.path(row['path'],True)
        trash_root=self.state/'trash'
        trash_root.mkdir(parents=True,exist_ok=True)
        recovery=uuid.uuid4().hex
        bucket=trash_root/recovery
        bucket.mkdir(mode=0o700)
        dst=bucket/src.name
        record={'original_path':row['path'],'deleted_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
                'file_id':file_id}
        try:
            os.replace(src,dst)
            (bucket/'record.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
            sync_dir(bucket);sync_dir(trash_root);sync_dir(src.parent)
        except Exception:
            try:
                if dst.exists() and not src.exists():os.replace(dst,src)
            finally:
                try:(bucket/'record.json').unlink(missing_ok=True)
                except Exception:pass
                try:bucket.rmdir()
                except Exception:pass
            raise
        self.index()
        graph=self.retrieval().rebuild_graph()
        self._snapshot_catalog()
        self._log_mutation('delete',file_id,row['path'],'',True,True,json.dumps(graph,separators=(',',':')))
        return {'deleted':True,'path':row['path'],'recovery_id':recovery}


    def folders(self):
        folders=[]
        for base,dirs,_ in os.walk(self.root,followlinks=False):
            dirs[:]=sorted(x for x in dirs if not (Path(base)/x).is_symlink() and not x.startswith('.'))
            for name in dirs:
                rel=(Path(base)/name).relative_to(self.root).as_posix()
                folders.append(rel)
        return folders

    def create_folder(self,path):
        if not isinstance(path,str) or not path.strip():raise ValueError('Folder name is required')
        clean=path.strip().strip('/')
        target=self.path(clean)
        if target.exists():raise ValueError('A file or folder already exists there')
        target.mkdir(parents=False,mode=0o755)
        sync_dir(target.parent)
        return {'created':True,'path':clean}

    def rename_file(self,file_id,new_name):
        row=self.row(file_id)
        if not isinstance(new_name,str):raise ValueError('Invalid name')
        new_name=new_name.strip()
        if not new_name or new_name in ('.','..') or '/' in new_name or '\\' in new_name or new_name.startswith('.'):
            raise ValueError('Use one visible filename')
        suffix=Path(new_name).suffix.lower()
        if suffix not in ('.txt','.md','.csv','.json','.py','.pdf'):
            raise ValueError('Unsupported file type')
        destination=(Path(row['path']).parent/new_name).as_posix()
        result=super().move_file(file_id,destination,row['hash'],uuid.uuid4().hex)
        # Rename/move does not change content, so metadata and semantic embedding stay valid.
        self._sync_path_mutation('rename',file_id,row['path'],result['path'])
        return {'renamed':True,'file_id':file_id,'path':result['path']}

    def move_to_folder(self,file_id,folder):
        row=self.row(file_id)
        folder=(folder or '').strip().strip('/')
        if folder:
            target=self.path(folder)
            if not target.is_dir():raise ValueError('Destination folder does not exist')
        destination=(Path(folder)/Path(row['path']).name).as_posix() if folder else Path(row['path']).name
        result=super().move_file(file_id,destination,row['hash'],uuid.uuid4().hex)
        self._sync_path_mutation('move',file_id,row['path'],result['path'])
        return {'moved':True,'file_id':file_id,'path':result['path']}

    def move_many(self,moves):
        """Apply many path-only moves, then rebuild the graph once.

        Used by the organizer so an 80-file plan does not trigger 80 O(n^2) graph
        rebuilds. All destinations are validated before the first mutation.
        """
        if not isinstance(moves,list) or not moves:raise ValueError('moves must be a non-empty list')
        planned=[];destinations=set()
        for item in moves:
            file_id=item.get('file_id');folder=(item.get('folder') or '').strip().strip('/')
            row=self.row(file_id)
            if folder:
                target=self.path(folder)
                if not target.is_dir():raise ValueError('Destination folder does not exist: '+folder)
            destination=(Path(folder)/Path(row['path']).name).as_posix() if folder else Path(row['path']).name
            dst=self.path(destination)
            if dst.exists() or destination in destinations:raise ValueError('Destination exists: '+destination)
            destinations.add(destination);planned.append((file_id,row,destination))
        results=[]
        for file_id,row,destination in planned:
            result=super().move_file(file_id,destination,row['hash'],uuid.uuid4().hex)
            results.append({'file_id':file_id,'from':row['path'],'path':result['path']})
        graph=self.retrieval().rebuild_graph()
        self._snapshot_catalog()
        self._log_mutation('batch_move',None,'','',False,True,json.dumps({'count':len(results),'graph':graph},separators=(',',':')))
        return {'moved':results,'count':len(results),'graph':graph}

    def duplicate_file(self,file_id):
        row=self.row(file_id);src=self.path(row['path'],True)
        parent=Path(row['path']).parent;stem=Path(row['path']).stem;suffix=Path(row['path']).suffix
        n=1
        while True:
            label=' copy' if n==1 else f' copy {n}'
            rel=(parent/(stem+label+suffix)).as_posix()
            dst=self.path(rel)
            if not dst.exists():break
            n+=1
        import shutil
        shutil.copy2(src,dst,follow_symlinks=False);sync_dir(dst.parent);self.index()
        created=self.db.execute('SELECT id FROM files WHERE path=?',(rel,)).fetchone()
        new_id=created['id'] if created else None
        if new_id:
            meta=self.db.execute('SELECT * FROM metadata WHERE file_id=? AND content_hash=?',(file_id,row['hash'])).fetchone()
            if meta:
                self.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?,?,?,?,?,?,?,?)',
                    (new_id,meta['content_hash'],meta['summary'],meta['keywords'],meta['project'],meta['document_type'],meta['status'],meta['model'],meta['error']))
            emb=self.db.execute('SELECT * FROM embeddings WHERE file_id=? AND content_hash=?',(file_id,row['hash'])).fetchone()
            if emb:
                self.db.execute('INSERT OR REPLACE INTO embeddings VALUES(?,?,?,?,?,?,?,?,?)',
                    (new_id,emb['content_hash'],emb['source_hash'],emb['provider'],emb['model'],emb['dimensions'],emb['semantic'],emb['vector'],time.time()))
            self.db.commit()
            self._sync_path_mutation('duplicate',new_id,row['path'],rel)
        return {'duplicated':True,'path':rel,'file_id':new_id}

    def shortcut(self,file_id,enabled=True):
        self.row(file_id)
        if enabled:self.db.execute('INSERT OR IGNORE INTO shortcuts VALUES(?)',(file_id,))
        else:self.db.execute('DELETE FROM shortcuts WHERE file_id=?',(file_id,))
        self.db.commit();return {'shortcut':bool(enabled)}

    def trash_items(self):
        root=self.state/'trash';items=[]
        if not root.exists():return items
        for bucket in sorted(root.iterdir(),key=lambda p:p.stat().st_mtime,reverse=True):
            try:
                record=json.loads((bucket/'record.json').read_text(encoding='utf-8'))
                payload=bucket/'file'
                if not payload.exists():
                    candidates=[x for x in bucket.iterdir() if x.name!='record.json']
                    payload=candidates[0] if candidates else None
                items.append({'recovery_id':bucket.name,'original_path':record.get('original_path',''),
                    'deleted_utc':record.get('deleted_utc',''),'size':payload.stat().st_size if payload and payload.exists() else 0})
            except Exception:continue
        return items

    def restore_trash(self,recovery_id):
        if not isinstance(recovery_id,str) or not re.fullmatch(r'[0-9a-f]{32}',recovery_id):raise ValueError('Invalid recovery ID')
        bucket=self.state/'trash'/recovery_id
        record=json.loads((bucket/'record.json').read_text(encoding='utf-8'))
        rel=record['original_path'];dst=self.path(rel)
        if dst.exists():raise ValueError('Restore destination already exists')
        candidates=[x for x in bucket.iterdir() if x.name!='record.json']
        if len(candidates)!=1:raise ValueError('Trash item is incomplete')
        dst.parent.mkdir(parents=True,exist_ok=True);os.replace(candidates[0],dst);sync_dir(dst.parent)
        (bucket/'record.json').unlink();bucket.rmdir();self.index()
        restored=self.db.execute('SELECT id FROM files WHERE path=?',(rel,)).fetchone()
        restored_id=restored['id'] if restored else None
        self._sync_content_mutation('restore',restored_id,'',rel)
        self.retrieval().rebuild_graph()
        return {'restored':True,'path':rel,'file_id':restored_id}

    def consolidate_files(self,file_ids,destination):
        """Create one Markdown file containing the current contents of selected files.

        This is non-destructive: source files are preserved. The resulting file goes
        through the normal write/sync path so SQLite, Qwen metadata, embeddings and
        graph state cannot silently diverge.
        """
        if not isinstance(file_ids,list) or not file_ids:
            raise ValueError('file_ids must be a non-empty list')
        if len(file_ids)>64:
            raise ValueError('consolidation is capped at 64 source files')
        if not isinstance(destination,str) or not destination.strip():
            raise ValueError('destination is required')
        destination=destination.strip().strip('/')
        if '/' in destination:
            # Destination may include a safe folder, but no traversal.
            self.path(destination)
        if not Path(destination).suffix:
            destination += '.md'
        if Path(destination).suffix.lower() not in ('.md','.txt'):
            raise ValueError('consolidated output must be .md or .txt')
        parts=['# Consolidated workspace files','']
        seen=set()
        for file_id in file_ids:
            if file_id in seen:continue
            row=self.row(file_id)
            if row['path']==destination:
                continue
            seen.add(file_id)
            data=self.read_file(file_id,0,32768)
            content=data.get('content','')
            parts.extend(['## '+row['path'],'',content.rstrip(),''])
        if not seen:raise ValueError('No source files remain after excluding the destination')
        text='\n'.join(parts).rstrip()+'\n'
        existing=self.db.execute('SELECT hash FROM files WHERE path=?',(destination,)).fetchone()
        expected=existing['hash'] if existing else ''
        result=self.write_file(destination,text,expected,uuid.uuid4().hex)
        return {'consolidated':True,'path':destination,'sources':len(seen),'result':result}

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
        raw_terms=re.findall(r'\w+',query.casefold())
        stop={'find','show','give','get','me','my','the','a','an','all','everything','anything',
              'related','relevant','about','for','to','of','in','on','with','and','or','files','file',
              'documents','document','stuff','things','please','workspace'}
        terms={t for t in raw_terms if len(t)>1 and t not in stop}
        if not terms:terms=set(raw_terms)
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

    def retrieval(self):
        """Return one cached retrieval service bound to this catalog connection."""
        service=getattr(self,'_retrieval_service',None)
        if service is None:
            service=RetrievalService(self)
            self._retrieval_service=service
        return service

    def retrieve(self,query,limit=0,debug=False,include_excerpts=False,max_chars=12000):
        return self.retrieval().retrieve(query,limit=limit,debug=debug,
            include_excerpts=include_excerpts,max_chars=max_chars)

    def retrieval_status(self):
        return self.retrieval().status()

    def warm_retrieval(self):
        return self.retrieval().warm()

    def rebuild_retrieval(self,limit=200,force=False):
        """Rebuild a snapshot of files needing embeddings, then rebuild the graph once.

        Each file is attempted at most once per invocation. A fallback embedding remains
        eligible for a later retry, but cannot cause the current rebuild to loop forever.
        """
        service=self.retrieval()
        service.cleanup()
        batch=max(1,min(int(limit),200))
        if force:
            rows=self.db.execute("SELECT id FROM files ORDER BY path").fetchall()
        else:
            target=service.embedder.target_identity()
            rows=self.db.execute("""SELECT f.id FROM files f LEFT JOIN embeddings e ON e.file_id=f.id
                WHERE e.file_id IS NULL OR e.content_hash!=f.hash
                   OR e.provider!=? OR e.model!=? OR e.semantic!=?
                ORDER BY f.path""",
                (target["provider"],target["model"],1 if target["semantic"] else 0)).fetchall()
        targets=[row["id"] for row in rows]
        indexed=[]
        for start_at in range(0,len(targets),batch):
            for file_id in targets[start_at:start_at+batch]:
                indexed.append(service.index_file(file_id,force=force))
        graph=service.rebuild_graph()
        status=service.status()
        fallbacks=sum(1 for item in indexed if item.get("provider")=="hashed-fallback")
        semantics=sum(1 for item in indexed if item.get("semantic"))
        return {"indexed":indexed,"indexed_count":len(indexed),"semantic_indexed":semantics,
                "fallback_indexed":fallbacks,"graph":graph,"status":status}

    def dispatch(self,name,args,call_id=None):
        own={'find_documents','related_files','timeline','retrieve','retrieval_status','warm_retrieval','rebuild_retrieval','sync_status'}
        if name in own:return getattr(self,name)(**args)
        return super().dispatch(name,args,call_id)
