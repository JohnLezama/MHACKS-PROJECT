#!/bin/sh
set -eu
cat /etc/os-release
uname -m
systemctl is-active whiteboard-prepare whiteboardos whiteboard-ollama
/usr/bin/python3 - <<'CODE'
import sqlite3,ssl,json,urllib.request
from zoneinfo import ZoneInfo
print('Timezone:',ZoneInfo('America/Chicago'))
db=sqlite3.connect(':memory:');db.execute('create virtual table t using fts5(body)');print('FTS5 ready')
with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=20) as r:
 s=json.load(r);print('UI:',s['name'],'files:',len(s['files']),'model:',s['model']['status'])
with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=20) as r:print('Models:',json.load(r))
CODE
ollama show whiteboard-metadata:latest
nmcli device status
df -h / /data
