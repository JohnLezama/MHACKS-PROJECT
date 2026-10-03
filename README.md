# WhiteBoardOS — independent Pi 4 Linux distribution

This is the source/build project for WhiteBoardOS 0.3, a Linux distribution for a Raspberry Pi 4B with 4 GB RAM. It builds its own root filesystem with Buildroot. **It does not install onto Raspberry Pi OS, and needs no Raspberry Pi OS image.** It keeps the upstream Linux kernel, Pi firmware and drivers; those components are necessary hardware support.

**Delivery status:** source and build scripts, not an already compiled or hardware-tested SD image. The build must run on a Linux computer with internet access. Large third-party assets are intentionally absent. Scripts download them on your computer, or you can place them in the asset folders yourself. Compilation, Pi boot, Wi-Fi, SSH and real inference have not been tested here. The build can still reveal dependency or hardware issues.

Start with **[START-HERE.md](START-HERE.md)** for exact Windows/WSL steps.

## What the build produces

`dist/WhiteBoardOS-Pi4.img` is the file to flash with Raspberry Pi Imager's custom-image option. The ZIP is not flashable. The image has:

| Partition | Contents |
| --- | --- |
| FAT boot, 128 MiB | Pi firmware, ARM64 kernel, Pi4 device tree and boot configuration |
| ext4 root, 4 GiB | Our independently assembled userspace, Python, systemd, network tools, Ollama runtime, WhiteBoardOS backend and interface |
| ext4 data, 4 GiB | Qwen weights, imported documents, SQLite catalog, activity, saved tasks, settings and SSH host keys |

Use a **16 GB or larger SD card**, preferably 32 GB. The initial data filesystem uses 4 GiB; this version does not automatically expand to fill a larger card. An image contains your public key and Wi-Fi credentials and should be treated as a personal device image.

## Headless interface and shell

The Pi does all file indexing, local inference and Gemini tool execution. Your laptop's browser displays the interface over an SSH tunnel. No HDMI display is required. This version deliberately has **no on-Pi graphical compositor/browser**; it is a headless distribution with a browser-based WhiteBoardOS shell. The previous Chromium/Openbox/Raspberry Pi OS installer is not included.

SSH administration uses `root` with your public key and password login disabled. The agent and model services run as the separate unprivileged `whiteboard` account. Gemini is not given a root shell. The `wb` command is a small WhiteBoardOS command interface:

```sh
wb status
wb files
wb find 'Raspberry Pi audio'
wb find 'Raspberry Pi audio' --yesterday
wb ask 'Find PDFs about Raspberry Pi audio'
wb tasks
```

`wb ask` starts a task; watch and approve changes in the browser. Normal Linux administrative tools are still available over SSH.

## Included agent functionality

- Branded whiteboard interface, document cards, library, activity, tasks, PDF/text viewer, import, pinning, settings, Wi-Fi management and power controls.
- Qwen2.5 0.5B instruction model configured for metadata: 2048-token context, 180 output tokens, temperature 0, three CPU threads in requests, one loaded model/parallel request, low CPU priority and a 2 GiB service memory cap. Actual Pi performance must be measured.
- Metadata keyed by file hash, background updates, bounded text/PDF extraction, original content fallback when inference fails.
- Keyword retrieval over text and generated summaries/keywords, with human-view timestamps and calendar-day filters. **No embedding/vector search is implemented.** A tiny local model may produce weak summaries; the system checks output structure, not factual perfection.
- Small SQLite relationship tables: only explicitly supported project links are added, with source hashes.
- Gemini function calling for local file operations, user approval for mutations, hashes to reject stale edits, operation journal, retry IDs, supported undo and durable task checkpoints.
- Loopback-only HTTP, valid Host checks, mutation tokens and no browser-accessible arbitrary shell.

“Find that PDF I was looking at yesterday about Raspberry Pi audio” needs a real human opening event from the previous day in the selected timezone. A file's modification time does not prove it was viewed. Demo documents are seeded; no fake yesterday activity is seeded.

## Build organization

`buildroot-external/` is a Buildroot external project. `scripts/build.sh` starts with the release's official `raspberrypi4_64_defconfig` solely for the firmware/kernel hardware support, then replaces the userspace choices and image hooks with our configuration. This is a board support configuration, not Raspberry Pi OS.

The external project adds systemd services, users, independent release identification, network settings and image assembly. Python checks that required options survive Kconfig dependency resolution; it fails rather than silently creating an image without required packages. SQLite configure options explicitly enable FTS5. `post-build.sh` installs our application and the supplied ARM64 runtime; `post-image.sh` produces the boot/root/data image using genimage and host e2fsprogs.

Buildroot 2026.08 is pinned for the source tree, whose board configuration pins its hardware sources. The Ollama convenience download URL and Qwen `main` URL are upstream moving sources; local downloaded checksums are recorded, but not independently authenticated release pins. Retain the downloaded assets and their checksums to reproduce that build.

## Validation and limits

See [TESTING.md](TESTING.md). Application and asset-handling tests can run locally without compiling the OS. Actual first boot still needs the Pi. Once running, execute `whiteboard-check` and use the hardware checklist in START-HERE.md. This is a hackathon prototype, not a claimed performance improvement or a production appliance.

## Sources and licenses

- [Buildroot manual](https://buildroot.org/downloads/manual/manual.html): independent cross-compiled system and external project hooks.
- [Buildroot releases](https://buildroot.org/download.html) and [upstream Pi board configurations](https://github.com/buildroot/buildroot/tree/master/configs).
- [Ollama Linux installation](https://docs.ollama.com/linux), [GGUF import](https://docs.ollama.com/import), [Modelfile reference](https://docs.ollama.com/modelfile).
- [Official Qwen GGUF files](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/tree/main).
- [Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling).

Linux, firmware, Buildroot, Ollama and models retain their respective licenses. Before distributing a built image, run `make legal-info` in its Buildroot output directory and retain the third-party licenses and corresponding sources required by their licenses. This ZIP contains our sources and demo files, not those downloaded components.
