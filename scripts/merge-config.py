#!/usr/bin/env python3
"""Start with upstream hardware support, then apply our userspace and image hooks."""
import json,re,sys
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
def entries(text):
 out={}
 for line in text.splitlines():
  if line.startswith('BR2_') and '=' in line:
   k,v=line.split('=',1);out[k]=v
  elif line.startswith('# BR2_') and line.endswith(' is not set'):
   out[line[2:-11]]='n'
 return out
def main():
 br,output=map(Path,sys.argv[1:]);board=BASE/'buildroot-external/board/pi4'
 config=entries((output/'.config').read_text())
 if config.get('BR2_aarch64')!='y':raise SystemExit('Upstream configuration is not ARM64')
 requested=entries((board/'whiteboard.fragment').read_text())
 # Check actual release symbols; do not silently drop requested functionality.
 definitions='\n'.join(p.read_text(errors='replace') for p in br.rglob('Config.in*'))
 symbols=set(re.findall(r'^\s*(?:menuconfig|config)\s+(BR2_\w+)',definitions,re.M))
 aliases={'BR2_PACKAGE_SQLITE_FTS5':['BR2_PACKAGE_SQLITE_ENABLE_FTS5'],
          'BR2_PACKAGE_NETWORK_MANAGER_WIFI':['BR2_PACKAGE_NETWORK_MANAGER_WIRELESS']}
 for k in list(requested):
  if k in symbols:continue
  found=next((x for x in aliases.get(k,[]) if x in symbols),None)
  if found:requested[found]=requested.pop(k)
  elif k=='BR2_PACKAGE_SQLITE_FTS5':
   # Some releases unconditionally enable FTS5. Verify the package's configure flags.
   if '--enable-fts5' not in (br/'package/sqlite/sqlite.mk').read_text():
    raise SystemExit('SQLite FTS5 option is unavailable; inspect package/sqlite before building')
   requested.pop(k)
  else:raise SystemExit('Buildroot symbol unavailable: '+k+'; configuration requires review')
 def quoted(s):return json.dumps(str(s))
 requested.update({
  'BR2_ROOTFS_OVERLAY':quoted(board/'overlay'),
  'BR2_ROOTFS_USERS_TABLES':quoted(board/'users.txt'),
  'BR2_ROOTFS_POST_BUILD_SCRIPT':quoted(board/'post-build.sh'),
  'BR2_ROOTFS_POST_IMAGE_SCRIPT':quoted(board/'post-image.sh'),
  'BR2_ROOTFS_POST_SCRIPT_ARGS':'""',
  'BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES':quoted(board/'kernel.fragment'),
  'BR2_TARGET_GENERIC_ROOT_PASSWD':quoted(json.loads((BASE/'settings/device.json').read_text())['root_password_hash'])})
 # Reset mutually exclusive choices from the upstream BusyBox/uClibc profile.
 for k in ('BR2_TOOLCHAIN_BUILDROOT_UCLIBC','BR2_TOOLCHAIN_BUILDROOT_MUSL','BR2_INIT_BUSYBOX',
           'BR2_INIT_SYSV','BR2_ROOTFS_DEVICE_CREATION_DYNAMIC_MDEV','BR2_PACKAGE_IFUPDOWN_SCRIPTS'):
  config[k]='n'
 # Preserve the ARM64 board firmware/kernel configuration, replace application choices.
 config.update(requested)
 (output/'.config').write_text(''.join(f'# {k} is not set\n' if v=='n' else f'{k}={v}\n' for k,v in config.items()))
 (output/'whiteboard.requested.json').write_text(json.dumps(requested,indent=2))
if __name__=='__main__':main()
