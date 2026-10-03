#!/usr/bin/env python3
import json,sys
from pathlib import Path
from importlib.machinery import SourceFileLoader
merge=SourceFileLoader('merge',str(Path(__file__).with_name('merge-config.py'))).load_module()
actual=merge.entries(Path(sys.argv[1]).read_text());requested=json.loads(Path(sys.argv[2]).read_text())
missing=[k for k,v in requested.items() if actual.get(k)!=v]
if missing:raise SystemExit('Kconfig disabled/changed required options: '+', '.join(missing)+'\nInspect work/output/.config and dependencies before compiling.')
print('Required Buildroot configuration survived dependency resolution.')
