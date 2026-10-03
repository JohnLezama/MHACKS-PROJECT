#!/usr/bin/env bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ $EUID == 0 ]]; then echo 'Build as your regular Linux user, not sudo.' >&2; exit 1; fi
if [[ "$project_dir" == *' '* || "$project_dir" == /mnt/* ]]; then
 echo 'Extract into your Linux home directory (no spaces), not a Windows /mnt drive.' >&2; exit 1
fi
python3 "$project_dir/scripts/check-assets.py"
version=2026.08
archive="$project_dir/assets/buildroot/buildroot-$version.tar.xz"
buildroot_dir="$project_dir/work/buildroot-$version"
output_dir="$project_dir/work/output"
mkdir -p "$project_dir/work" "$project_dir/dist"
if [[ ! -d "$buildroot_dir" ]]; then
 if [[ ! -s "$archive" ]]; then
  curl --fail --location --retry 3 "https://buildroot.org/downloads/buildroot-$version.tar.xz" -o "$archive.partial"
  mv "$archive.partial" "$archive"
 fi
 tar -xJf "$archive" -C "$project_dir/work"
fi
external_dir="$project_dir/buildroot-external"
make -C "$buildroot_dir" O="$output_dir" BR2_EXTERNAL="$external_dir" raspberrypi4_64_defconfig
python3 "$project_dir/scripts/merge-config.py" "$buildroot_dir" "$output_dir"
make -C "$buildroot_dir" O="$output_dir" BR2_EXTERNAL="$external_dir" olddefconfig
python3 "$project_dir/scripts/verify-config.py" "$output_dir/.config" "$output_dir/whiteboard.requested.json"
make -C "$buildroot_dir" O="$output_dir" BR2_EXTERNAL="$external_dir" -j"${WB_JOBS:-4}"
cp "$output_dir/images/WhiteBoardOS-Pi4.img" "$project_dir/dist/WhiteBoardOS-Pi4.img"
(cd "$project_dir/dist" && sha256sum WhiteBoardOS-Pi4.img > WhiteBoardOS-Pi4.img.sha256)
echo 'Image ready: dist/WhiteBoardOS-Pi4.img. Flash this file, not the ZIP.'
