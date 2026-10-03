# Exact manual steps — Windows laptop, headless Raspberry Pi

**You do not install Raspberry Pi OS. You do not install Qwen on the Pi manually.** Supply its model file before building; the image includes it and registers it automatically at first boot.

You need a Pi 4B (4 GB), a suitable USB-C power supply, a 16 GB+ microSD card, an SD reader and a network shared with the laptop. Prefer a personal WPA2 hotspot or a home router. Enterprise/captive-portal Wi-Fi is not configured by this setup flow; client isolation on event Wi-Fi can prevent SSH. Ethernet to a router is another option. This is not a USB-network-gadget setup.

## 1. Install the build environment

In Windows PowerShell as administrator:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart if requested. Open Ubuntu and finish its username/password setup. You also need **Raspberry Pi Imager installed on Windows** to flash the final image: https://www.raspberrypi.com/software/

In Ubuntu:

```bash
sudo apt update
sudo apt install -y build-essential git wget curl ca-certificates unzip rsync file bc cpio \
  python3 python3-pip libncurses-dev bzip2 xz-utils zstd patch perl openssl \
  openssh-client gawk diffutils findutils sed gzip tar debianutils
```

The project requires Python 3.11+; Ubuntu 24.04 includes a suitable Python. Plan for **at least 50 GB of free Linux disk space** and several hours for an initial build, depending on CPU and downloads. Set `WB_JOBS=2` on a low-memory laptop; the default is four build jobs. The target model uses the Pi CPU, not a laptop GPU.

## 2. Extract the project into the Linux filesystem

Download WhiteBoardOS-Buildroot-Pi4-source.zip on Windows. In Ubuntu, replace `YOUR_WINDOWS_USER` with your Windows folder name:

```bash
cd ~
unzip /mnt/c/Users/YOUR_WINDOWS_USER/Downloads/WhiteBoardOS-Buildroot-Pi4-source.zip
cd ~/WhiteBoardOS-buildroot
```

Keep the build under `~/WhiteBoardOS-buildroot`, not under `/mnt/c/`, OneDrive or a path containing spaces. Do not use the earlier Raspberry Pi OS provision script.

## 3. Supply the two intentionally missing AI assets

Recommended: let these scripts download on your computer:

```bash
bash scripts/fetch-ollama.sh
bash scripts/fetch-qwen.sh
```

This downloads **Linux ARM64 Ollama** for the image and **Qwen2.5-0.5B-Instruct Q4_K_M GGUF** (the official Qwen file is approximately 491 MB). You do not need Windows Ollama installed for this route. Do not try to execute the ARM64 binary on an x86 Windows/WSL laptop.

If the download scripts fail, download the files in your browser and supply them manually:

| Missing asset | Download | Exact destination |
| --- | --- | --- |
| Ollama runtime | https://ollama.com/download/ollama-linux-arm64.tar.zst | Extract its contents into `assets/ollama-runtime/`: `bin/ollama` and the complete `lib/ollama/` from the same archive |
| Qwen weights | https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/blob/main/qwen2.5-0.5b-instruct-q4_k_m.gguf | `assets/qwen2.5-0.5b-instruct-q4_k_m.gguf` |
| Buildroot source, optional fallback | https://buildroot.org/downloads/buildroot-2026.08.tar.xz | `assets/buildroot/buildroot-2026.08.tar.xz` |

For a manually downloaded Ollama archive:

```bash
tar --zstd -xf /mnt/c/Users/YOUR_WINDOWS_USER/Downloads/ollama-linux-arm64.tar.zst \
  -C assets/ollama-runtime
```

Use the Linux ARM64 archive, not the Windows installer or Linux AMD64 binary. Keep its library directory; copying only `ollama` is insufficient. Do not clone the entire Qwen repository (it contains other quantizations).

Alternative if you already have native Ollama on Windows/Linux: prepare and export an Ollama model store instead of using GGUF. Instructions are in `docs/EXPORT-MODEL.md`. Choose one model supply route.

## 4. Set your headless access before building

Generate a key **in Ubuntu** if you do not already have one there:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/whiteboard_pi
```

Keep the private key on your computer. Supply only the `.pub` file:

```bash
python3 scripts/configure.py --ssh-key ~/.ssh/whiteboard_pi.pub
```

Enter the Wi-Fi SSID, password and country when prompted. This supports personal Wi-Fi networks; use a WPA2-compatible hotspot if unsure. For Ethernet only:

```bash
python3 scripts/configure.py --ssh-key ~/.ssh/whiteboard_pi.pub --ethernet-only
```

The generated `settings/device.json` is private and contains Wi-Fi credentials. The image uses key-only SSH, with no shared default password. Gemini's API key is entered later in the interface.

## 5. Check and build

```bash
python3 scripts/check-assets.py
bash scripts/build.sh
```

Buildroot automatically downloads its sources and cross-compiles the kernel, root filesystem and dependencies. **No Raspberry Pi OS base image is needed.** Build as your regular user, without sudo. If an option check or compilation fails, retain the full error output; the source project has not been compiled here and may need a compatibility fix.

Success produces:

```text
dist/WhiteBoardOS-Pi4.img
dist/WhiteBoardOS-Pi4.img.sha256
```

The filesystem contents are private to this device configuration. For a simple way to copy the output into Windows Downloads:

```bash
cp dist/WhiteBoardOS-Pi4.img /mnt/c/Users/YOUR_WINDOWS_USER/Downloads/
```

## 6. Flash the SD card

Open Raspberry Pi Imager on Windows. Choose your Pi model, select **Use custom** (or the current custom-image option), and select **WhiteBoardOS-Pi4.img**. Select your SD card and write/verify it. Writing replaces the selected card's contents.

Do not use Imager's Raspberry Pi OS customization for Wi-Fi/SSH; our build already set them. Eject the card, insert it into the Pi and power it on. No monitor is needed.

## 7. Connect and open WhiteBoardOS

Find the Pi's IP address in your router/hotspot connected-device list; this image does not include mDNS and `whiteboardos.local` is **not guaranteed to resolve**. Replace `PI_IP` below.

In Ubuntu on your laptop:

```bash
ssh -i ~/.ssh/whiteboard_pi -L 8765:127.0.0.1:8765 root@PI_IP
```

Accept the host key after confirming this is your Pi. Keep this session open. In your Windows browser open:

```text
http://localhost:8765
```

On standard WSL2 networking, Windows forwards Linux localhost ports. If unavailable, run the SSH tunnel directly in PowerShell using the same private key copied locally, or configure WSL localhost forwarding. The HTTP listener remains on the Pi's loopback interface.

On the Pi, via the SSH terminal:

```sh
whiteboard-check
wb status
```

The GGUF route registers `whiteboard-metadata:latest` automatically on first boot; the interface may initially say local model unavailable. Allow the import to finish, then inspect:

```sh
journalctl -u whiteboard-model-import -u whiteboard-ollama -u whiteboardos --no-pager
```

In WhiteBoardOS Settings enter your **Gemini API key**, choose a model your account supports and set your timezone. Local indexing uses Qwen without Gemini. Agent tasks need internet and valid Gemini credentials.

## 8. Physical-device acceptance checks

- `cat /etc/os-release` identifies WhiteBoardOS; `uname -m` reports `aarch64`.
- Wi-Fi/Ethernet obtains an IP, internet works, and system time is correct (TLS and “yesterday” depend on this).
- SSH succeeds with your key, and `whiteboard-check` reports active services, FTS5 and the metadata model.
- The interface opens through the tunnel; import a text file/PDF, wait for metadata and search for its subject.
- Open a document in the viewer and confirm that Activity records it. A search for yesterday requires actual previous-day activity.
- Start a Gemini task. A proposed write/move requires your approval and supported undo preserves the original.
- Reboot and reconnect. Documents, catalog, tasks, settings and model stay present.

If SSH does not connect: check the hotspot's client list, isolation, selected network, power and SD verification. Ethernet is the easiest fallback when Wi-Fi debugging is inaccessible. If a service fails, use its `journalctl` output; a source ZIP cannot establish that the Pi booted successfully.
