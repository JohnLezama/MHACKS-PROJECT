#!/usr/bin/env python3
import json,sys,uuid
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
s=json.loads((BASE/'settings/device.json').read_text());target=Path(sys.argv[1])
def write(path,text,mode=0o644):
 p=target/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text);p.chmod(mode)
write('root/.ssh/authorized_keys',s['ssh_public_key']+'\n',0o600)
(target/'root/.ssh').chmod(0o700)
write('etc/modprobe.d/whiteboard-wifi.conf','options cfg80211 ieee80211_regdom='+s['country']+'\n')
write('etc/NetworkManager/conf.d/whiteboard.conf','[main]\nplugins=keyfile\ndhcp=internal\n[device]\nwifi.scan-rand-mac-address=no\n')
# Keyfile values escape separators and whitespace rather than interpolating shell text.
def escape(v):
 return v.replace('\\','\\\\').replace('\n','\\n').replace('\r','\\r').replace('\t','\\t').replace(';','\\;').replace(' ','\\s')
if s['ssid']:
 text='[connection]\nid=whiteboard-wifi\nuuid='+str(uuid.uuid4())+'\ntype=wifi\nautoconnect=true\n\n[wifi]\nmode=infrastructure\nssid='+escape(s['ssid'])+'\n\n[wifi-security]\nkey-mgmt=wpa-psk\npsk='+escape(s['wifi_password'])+'\n\n[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n'
 write('etc/NetworkManager/system-connections/whiteboard-wifi.nmconnection',text,0o600)
