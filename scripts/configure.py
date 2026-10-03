#!/usr/bin/env python3
"""Create private build settings; never puts a Gemini key in the image."""
import argparse,getpass,hashlib,json,re,secrets,subprocess
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--ssh-key',type=Path,required=True)
 p.add_argument('--ethernet-only',action='store_true');a=p.parse_args()
 key=a.ssh_key.expanduser().read_text().strip()
 if '\n' in key or not re.match(r'^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) [A-Za-z0-9+/=]+(?: .*)?$',key):
  p.error('Supply one public SSH key (.pub), never a private key')
 ssid='';password='';country='US'
 if not a.ethernet_only:
  ssid=input('Wi-Fi name (SSID): ').strip()
  if not 1<=len(ssid.encode())<=32 or any(c in ssid for c in '\n\r\0'):p.error('Invalid SSID')
  password=getpass.getpass('Wi-Fi password (WPA2/WPA3 personal): ')
  if not (8<=len(password)<=63 or re.fullmatch('[0-9a-fA-F]{64}',password)):p.error('Password must be 8–63 characters or 64 hex digits')
  country=input('Two-letter Wi-Fi country [US]: ').strip().upper() or 'US'
  if not re.fullmatch('[A-Z]{2}',country):p.error('Invalid country')
 # Random unknown password; SSH service only accepts keys. No default credential.
 digest=subprocess.run(['openssl','passwd','-6','-stdin'],input=secrets.token_urlsafe(48)+'\n',text=True,capture_output=True,check=True).stdout.strip()
 settings={'ssh_public_key':key,'ssid':ssid,'wifi_password':password,'country':country,'root_password_hash':digest}
 folder=BASE/'settings';folder.mkdir(exist_ok=True);folder.chmod(0o700)
 f=folder/'device.json';f.write_text(json.dumps(settings,indent=2)+'\n');f.chmod(0o600)
 print('Device settings ready. Contains Wi-Fi credentials; keep this folder private.')
if __name__=='__main__':main()
