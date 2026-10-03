#!/usr/bin/env python3
"""Run on Windows/Linux with a native Ollama installation and prepared metadata model."""
import argparse,hashlib,json,shutil
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--destination',type=Path,required=True)
a=p.parse_args();src=a.source.expanduser();dst=a.destination
manifest=Path('manifests/registry.ollama.ai/library/whiteboard-metadata/latest')
obj=json.loads((src/manifest).read_text());items=[obj['config']]+obj['layers']
for item in items:
 digest=item['digest'];algorithm,value=digest.split(':',1)
 if algorithm!='sha256' or len(value)!=64:raise SystemExit('Invalid manifest digest')
 rel=Path('blobs')/('sha256-'+value);blob=src/rel
 with blob.open('rb') as f:
  if hashlib.file_digest(f,'sha256').hexdigest()!=value:raise SystemExit('Model blob checksum failed')
 (dst/rel).parent.mkdir(parents=True,exist_ok=True);shutil.copy2(blob,dst/rel)
(dst/manifest).parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src/manifest,dst/manifest)
print('Exported whiteboard-metadata and all referenced blobs. Weights work across CPU architectures.')
