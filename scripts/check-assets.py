#!/usr/bin/env python3
import argparse,hashlib,json,struct,sys
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
def check(base,allow_missing=False):
 errors=[];runtime=base/'assets/ollama-runtime';binary=runtime/'bin/ollama'
 if binary.is_file():
  with binary.open('rb') as f:h=f.read(64)
  if len(h)<20 or h[:4]!=b'\x7fELF' or h[4]!=2 or h[5]!=1 or struct.unpack('<H',h[18:20])[0]!=183:
   errors.append('bin/ollama must be a Linux ARM64 ELF executable, not Windows/x86 Ollama')
  if not (runtime/'lib/ollama').is_dir() or not any((runtime/'lib/ollama').rglob('*.so*')):
   errors.append('Runtime is incomplete: include lib/ollama from the same ARM64 archive')
 else:errors.append('Missing assets/ollama-runtime/bin/ollama (ARM64 archive contents)')
 models=base/'assets/ollama-models';gguf=base/'assets/qwen2.5-0.5b-instruct-q4_k_m.gguf'
 manifest=models/'manifests/registry.ollama.ai/library/whiteboard-metadata/latest'
 if manifest.is_file():
  try:
   obj=json.loads(manifest.read_text());parts=[obj['config']]+obj['layers']
   if not any(x.get('mediaType','').endswith('.model') for x in obj['layers']):raise ValueError('No model layer')
   for part in parts:
    digest=part['digest'];algorithm,value=digest.split(':',1)
    if algorithm!='sha256' or len(value)!=64:raise ValueError('Invalid blob digest')
    blob=models/'blobs'/('sha256-'+value)
    if not blob.is_file():raise ValueError('Missing referenced model blob: '+blob.name)
    with blob.open('rb') as stream:
     if hashlib.file_digest(stream,'sha256').hexdigest()!=value:raise ValueError('Blob checksum mismatch: '+blob.name)
  except (ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:errors.append(str(exc))
 elif gguf.is_file():
  with gguf.open('rb') as f:
   if f.read(4)!=b'GGUF':errors.append('Qwen file does not have a GGUF header')
  if gguf.stat().st_size<100_000_000:errors.append('GGUF is too small for the requested Qwen model')
 else:errors.append('Missing metadata model: add the exported model store or the named Qwen GGUF')
 if not (base/'settings/device.json').is_file():errors.append('Run scripts/configure.py with your SSH public key first')
 if errors and not allow_missing:raise SystemExit('\n'.join(errors))
 return errors
def main():
 p=argparse.ArgumentParser();p.add_argument('--allow-missing',action='store_true');a=p.parse_args()
 errors=check(BASE,a.allow_missing)
 if errors:print('Intentionally missing supplied assets:\n'+'\n'.join(errors))
 else:print('Runtime architecture, model checksums/header and settings checked.')
if __name__=='__main__':main()
