#!/usr/bin/env python3
"""Build FAT firmware, ext4 system and seeded ext4 data into one SD image."""
import hashlib,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
def main():
 images,host=map(Path,sys.argv[1:]);genimage=host/'bin/genimage'
 mkfs=next((p for p in (host/'sbin/mkfs.ext4',host/'bin/mkfs.ext4',host/'sbin/mke2fs',host/'bin/mke2fs') if p.exists()),None)
 if mkfs is None:raise SystemExit('Host e2fsprogs is missing')
 firmware=images/'rpi-firmware'
 for rel in ('start4.elf','fixup4.dat'):
  if not (firmware/rel).is_file():raise SystemExit('Missing Pi 4 boot firmware: '+rel)
 kernel=next((p for p in (images/'Image',images/'kernel8.img') if p.is_file()),None)
 dtb=next(iter(images.rglob('bcm2711-rpi-4-b.dtb')),None)
 rootfs=next((p for p in (images/'rootfs.ext4',images/'rootfs.ext2') if p.is_file()),None)
 if not kernel or not dtb or not rootfs:raise SystemExit('Kernel, Pi4 DTB or ext4 root filesystem missing')
 with tempfile.TemporaryDirectory(prefix='whiteboard-image-',dir=images) as temp:
  staging=Path(temp);payload=staging/'input';payload.mkdir();boot=staging/'boot';boot.mkdir()
  for item in firmware.iterdir():
   if item.name in ('config.txt','cmdline.txt'):continue
   dest=boot/item.name
   if item.is_dir():shutil.copytree(item,dest)
   elif item.is_file():shutil.copy2(item,dest)
  shutil.copy2(kernel,boot/'kernel8.img');shutil.copy2(dtb,boot/dtb.name)
  (boot/'config.txt').write_text('[all]\narm_64bit=1\nkernel=kernel8.img\ndevice_tree=bcm2711-rpi-4-b.dtb\nenable_uart=1\ndisable_overscan=1\ngpu_mem=32\n')
  (boot/'cmdline.txt').write_text('console=serial0,115200 console=tty1 root=/dev/mmcblk0p2 rootfstype=ext4 rootwait rw net.ifnames=0\n')
  data=staging/'data';state=data/'whiteboardos';state.mkdir(parents=True)
  store=BASE/'assets/ollama-models'
  if (store/'manifests').is_dir():
   for name in ('blobs','manifests'):shutil.copytree(store/name,state/'models'/name)
  else:
   shutil.copy2(BASE/'assets/qwen2.5-0.5b-instruct-q4_k_m.gguf',state/'qwen-source.gguf')
   text=(BASE/'models/Modelfile').read_text().replace('FROM qwen2.5:0.5b-instruct-q4_K_M','FROM /data/whiteboardos/qwen-source.gguf')
   (state/'Modelfile.gguf').write_text(text)
  seedbytes=sum(p.stat().st_size for p in data.rglob('*') if p.is_file())
  if seedbytes>3*1024**3:raise SystemExit('Model assets exceed 3 GiB; remove unrelated models from export')
  data_img=payload/'data.ext4'
  with data_img.open('wb') as f:f.truncate(4*1024**3)
  subprocess.run([str(mkfs),'-t','ext4','-F','-L','WBDATA','-d',str(data),str(data_img)],check=True)
  (payload/'rootfs.ext4').symlink_to(rootfs.resolve())
  cfg=staging/'genimage.cfg'
  cfg.write_text('image boot.vfat {\n vfat { }\n srcpath = '+json.dumps(str(boot))+'\n size = 128M\n}\n'
   'image WhiteBoardOS-Pi4.img {\n hdimage { }\n'
   ' partition boot { partition-type = 0xC bootable = true image = "boot.vfat" offset = 1M }\n'
   ' partition root { partition-type = 0x83 image = "rootfs.ext4" }\n'
   ' partition data { partition-type = 0x83 image = "data.ext4" }\n}\n')
  empty=staging/'empty';empty.mkdir()
  env=os.environ.copy();env['PATH']=str(host/'bin')+':'+str(host/'sbin')+':'+env.get('PATH','')
  subprocess.run([str(genimage),'--rootpath',str(empty),'--tmppath',str(staging/'tmp'),
   '--inputpath',str(payload),'--outputpath',str(images),'--config',str(cfg)],check=True,env=env)
  image=images/'WhiteBoardOS-Pi4.img'
  with image.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
  (images/'WhiteBoardOS-Pi4.img.sha256').write_text(digest+'  WhiteBoardOS-Pi4.img\n')
 print('WhiteBoardOS image assembled with firmware, kernel, independent rootfs and persistent model/data partition.')
if __name__=='__main__':main()
