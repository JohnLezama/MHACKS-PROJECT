#!/usr/bin/env python3
import argparse,csv,datetime as dt,getpass,json,os,sqlite3,time,urllib.request,urllib.error
from pathlib import Path
BASE='https://generativelanguage.googleapis.com/v1beta'
DEFAULT_MODEL='gemini-3.5-flash-lite'
DOCROOT=Path('/data/whiteboardos/documents')
STATE=Path('/data/whiteboardos/state')
KEYFILE=Path('/data/whiteboardos/.secrets/gemini_api_key')
DB=STATE/'workspace.sqlite'

def key():
 k=os.getenv('GEMINI_API_KEY','').strip()
 if k:return k,'env'
 if KEYFILE.exists():
  k=KEYFILE.read_text().strip()
  if k:return k,'persistent'
 k=getpass.getpass('Gemini API key (hidden): ').strip()
 return k,'prompt'

def request(model,k,payload,timeout=90):
 u=f'{BASE}/models/{model}:generateContent?key={k}'
 req=urllib.request.Request(u,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
 t=time.perf_counter()
 try:
  with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read()),time.perf_counter()-t,''
 except Exception as e:
  return None,time.perf_counter()-t,f'{type(e).__name__}: {e}'

def manifest(root):
 prefix=root.strip('/').rstrip('/')+'/'
 con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
 q='''SELECT f.id,f.path,m.summary,m.keywords,m.project,m.document_type,m.status
      FROM files f LEFT JOIN metadata m ON m.file_id=f.id WHERE f.path LIKE ? ORDER BY f.path'''
 rows=[]
 for r in con.execute(q,(prefix+'%',)):
  if r['path'].endswith('/ground_truth.json') or r['path'].endswith('ground_truth.json'):continue
  try: kws=json.loads(r['keywords']) if isinstance(r['keywords'],str) else (r['keywords'] or [])
  except Exception: kws=[]
  rows.append({'file_id':r['id'],'path':r['path'],'summary':r['summary'] or '',
               'keywords':kws,'project':r['project'] or '','type':r['document_type'] or '',
               'metadata_status':r['status'] or ''})
 con.close();return rows

def parse_json(text):
 s=text.strip()
 if s.startswith('```'):
  s=s.split('\n',1)[1];s=s.rsplit('```',1)[0]
 return json.loads(s)

def safe_rel_folder(folder,root=''):
 p=Path(folder)
 if p.is_absolute() or '..' in p.parts or any(x.startswith('.') for x in p.parts):raise ValueError('unsafe folder')
 clean=p.as_posix().strip('/')
 prefix=root.strip('/').rstrip('/')
 if prefix and (clean==prefix or clean.startswith(prefix+'/')):
  clean=clean[len(prefix):].strip('/')
 return clean

def wb_api(path,payload=None):
 with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=10) as r:state=json.load(r)
 req=urllib.request.Request('http://127.0.0.1:8765'+path,data=json.dumps(payload or {}).encode(),
  headers={'Content-Type':'application/json','X-WhiteBoard-Token':state['csrf']},method='POST')
 with urllib.request.urlopen(req,timeout=180) as r:return json.load(r)

def apply_plan(plan,root,rows):
 # All mutations go through WhiteBoardOS APIs so SQLite, graph state, operation
 # history, and background metadata refresh stay coherent. Folders are created
 # first, then all moves are committed through one batch endpoint so the graph
 # is rebuilt exactly once.
 ops=[];by_path={r['path']:r for r in rows}
 folders=[]
 for folder in plan.get('folders',[]):
  rel=safe_rel_folder(folder,root)
  full=(Path(root)/rel).as_posix() if rel else root
  if full not in folders:folders.append(full)
 for full in folders:
  try:wb_api('/api/folder',{'path':full});ops.append(('mkdir',full))
  except urllib.error.HTTPError as exc:
   body=exc.read().decode('utf-8','replace')
   if 'already exists' not in body:raise
 batch=[]
 for move in plan.get('moves',[]):
  src=move['path'];item=by_path.get(src)
  if not item:raise ValueError('unknown source in plan: '+src)
  rel=safe_rel_folder(move['folder'],root)
  full=(Path(root)/rel).as_posix() if rel else root
  batch.append({'file_id':item['file_id'],'folder':full})
 if batch:
  result=wb_api('/api/batch-move',{'moves':batch})
  for moved in result.get('moved',[]):ops.append(('move',moved['from'],moved['path']))
 return ops

def score(plan,root):
 gt=DOCROOT/root/'ground_truth.json'
 if not gt.exists():return None
 truth=json.loads(gt.read_text())['categories']
 proposed={m['path']:m['folder'].split('/')[-1] for m in plan.get('moves',[])}
 total=correct=0
 for path,cat in truth.items():
  total+=1
  if proposed.get(path)==cat:correct+=1
 return {'correct':correct,'total':total,'accuracy':correct/total if total else 0.0,'planned':len(proposed)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',default='OrganizerTest');ap.add_argument('--model',default=DEFAULT_MODEL);ap.add_argument('--apply',action='store_true');ap.add_argument('--timeout',type=int,default=90);a=ap.parse_args()
 rows=manifest(a.root)
 if not rows:ap.error('No files found under '+a.root)
 k,src=key();print('Gemini credential:',src);print('Files presented:',len(rows))
 system='''You are WhiteBoardOS's file organization planner. The user should not need to specify folders manually. You receive a compact manifest of files already understood by local metadata. Propose a clean organization plan. Return ONLY JSON with keys: folders (array of folder names RELATIVE to the provided root, e.g. MHacks rather than OrganizerTest/MHacks), moves (array of {path,folder,reason}, where folder is also relative to the root). Every input file should appear at most once. Never delete files. Never move anything outside the provided root. Prefer a small number of meaningful categories over one folder per file. Preserve filenames; this benchmark does not perform renames.'''
 prompt='Organize these files into sensible folders. Root: '+a.root+'\nManifest:\n'+json.dumps(rows,separators=(',',':'),ensure_ascii=False)
 payload={'systemInstruction':{'parts':[{'text':system}]},'contents':[{'role':'user','parts':[{'text':prompt}]}], 'generationConfig':{'temperature':0,'maxOutputTokens':8192,'responseMimeType':'application/json'}}
 data,secs,err=request(a.model,k,payload,a.timeout)
 if err:print(err);return 1
 usage=(data or {}).get('usageMetadata') or {};parts=((data.get('candidates') or [{}])[0].get('content') or {}).get('parts') or []
 text='\n'.join(p.get('text','') for p in parts if isinstance(p.get('text'),str))
 try:plan=parse_json(text)
 except Exception as e:print('Could not parse plan:',e);print(text);return 1
 metrics=score(plan,a.root)
 print(json.dumps(plan,indent=2,ensure_ascii=False))
 print('\nMeasurements:');print('  status: planned');print('  files:',len(rows));print('  api_requests: 1');print('  api_seconds: %.3f'%secs)
 for label,field in [('input_tokens','promptTokenCount'),('output_tokens','candidatesTokenCount'),('total_tokens','totalTokenCount')]:print('  %s: %s'%(label,usage.get(field,'not reported')))
 if metrics:print('  ground_truth_accuracy: %.1f%% (%d/%d)'%(metrics['accuracy']*100,metrics['correct'],metrics['total']))
 if a.apply:
  ops=apply_plan(plan,a.root,rows);print('  applied_operations:',len(ops));print('Plan applied inside',a.root);print('  graph_and_catalog_sync: automatic via WhiteBoardOS mutation API')
 else:print('  applied_operations: 0 (dry run; pass --apply to execute)')
 return 0
if __name__=='__main__':raise SystemExit(main())
