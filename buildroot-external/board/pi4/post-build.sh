#!/usr/bin/env bash
set -euo pipefail
target_dir=$1
board_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd "$board_dir/../../.." && pwd)
python3 "$project_dir/scripts/check-assets.py"
mkdir -p "$target_dir/opt/whiteboardos" "$target_dir/data" "$target_dir/usr/libexec"
cp -a "$project_dir/whiteboardos" "$project_dir/ui" "$project_dir/demo" "$project_dir/models" "$target_dir/opt/whiteboardos/"
cp -a "$project_dir/assets/ollama-runtime/bin/ollama" "$target_dir/usr/bin/ollama"
chmod 755 "$target_dir/usr/bin/ollama"
mkdir -p "$target_dir/usr/lib/ollama"
cp -a "$project_dir/assets/ollama-runtime/lib/ollama/." "$target_dir/usr/lib/ollama/"
for entry in whiteboard-prepare whiteboard-model-import; do
 install -m 755 "$board_dir/$entry" "$target_dir/usr/libexec/$entry"
done
install -m 755 "$project_dir/scripts/check-device.sh" "$target_dir/usr/bin/whiteboard-check"
install -m 755 "$project_dir/scripts/wb" "$target_dir/usr/bin/wb"
mkdir -p "$target_dir/root/.ssh" "$target_dir/etc/dropbear" "$target_dir/etc/NetworkManager/system-connections" \
 "$target_dir/etc/NetworkManager/conf.d" "$target_dir/etc/modprobe.d" "$target_dir/usr/share/polkit-1/rules.d"
python3 "$project_dir/scripts/install-settings.py" "$target_dir"
# Buildroot's default /var may be volatile; SSH host keys live on /data instead.
rm -rf "$target_dir/etc/dropbear"
ln -s /data/system/dropbear "$target_dir/etc/dropbear"
cat > "$target_dir/etc/systemd/system/dropbear.service" <<'UNIT'
[Unit]
Description=WhiteBoardOS SSH administration (public keys only)
Requires=whiteboard-prepare.service
After=whiteboard-prepare.service network.target
[Service]
ExecStart=/usr/sbin/dropbear -F -E -R -s -g -p 22
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
mkdir -p "$target_dir/etc/systemd/system/multi-user.target.wants"
for unit in whiteboard-prepare whiteboardos whiteboard-ollama whiteboard-model-import dropbear; do
 ln -sf "../$unit.service" "$target_dir/etc/systemd/system/multi-user.target.wants/$unit.service"
done
ln -sf /usr/lib/systemd/system/NetworkManager.service "$target_dir/etc/systemd/system/multi-user.target.wants/NetworkManager.service"
mkdir -p "$target_dir/etc/systemd/system/sysinit.target.wants"
ln -sf /usr/lib/systemd/system/systemd-timesyncd.service "$target_dir/etc/systemd/system/sysinit.target.wants/systemd-timesyncd.service"
# NetworkManager exclusively manages both Wi-Fi and wired interfaces.
for unit in systemd-networkd.service systemd-networkd.socket; do
 ln -sf /dev/null "$target_dir/etc/systemd/system/$unit"
done
cat > "$target_dir/usr/share/polkit-1/rules.d/50-whiteboard.rules" <<'RULES'
polkit.addRule(function(action, subject) {
 var allowed = ["org.freedesktop.NetworkManager.network-control",
 "org.freedesktop.NetworkManager.settings.modify.system",
 "org.freedesktop.NetworkManager.wifi.scan",
 "org.freedesktop.login1.power-off", "org.freedesktop.login1.reboot"];
 if (subject.user == "whiteboard" && allowed.indexOf(action.id) >= 0)
  return polkit.Result.YES;
});
RULES
printf 'WhiteBoardOS 0.3 - use wb or the interface through SSH port forwarding\n' > "$target_dir/etc/issue"
for path in usr/bin/python3 usr/bin/pdftotext usr/bin/nmcli usr/bin/ollama; do
 [[ -e "$target_dir/$path" ]] || { echo "Missing required image dependency: $path" >&2;exit 1; }
done
