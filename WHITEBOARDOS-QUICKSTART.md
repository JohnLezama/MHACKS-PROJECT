# WhiteBoardOS quick start (v2.7)

## 1. Start the ARM64 WhiteBoardOS VM on the Mac

```bash
cd ~/Desktop/WhiteBoardOS-VM
qemu-system-aarch64 \
  -machine virt \
  -cpu host \
  -accel hvf \
  -smp 4 \
  -m 4096 \
  -kernel Image \
  -append "root=/dev/vda2 rootfstype=ext4 rootwait rw console=ttyAMA0 net.ifnames=0" \
  -drive file=WhiteBoardOS-QEMU.img,format=raw,if=none,id=wb \
  -device virtio-blk-device,drive=wb \
  -netdev user,id=net0,hostfwd=tcp::2222-:22 \
  -device virtio-net-device,netdev=net0 \
  -nographic
```

Leave that Terminal window running.

## 2. Open the native WhiteBoardOS app on the Mac

```bash
pkill -f 'WhiteBoardOS.app/Contents/MacOS/WhiteBoardOS' 2>/dev/null || true
open ~/Downloads/WhiteBoardOS-NativeClipboard-Patch/WhiteBoardOS.app
```

## 3. One-command full-stack test

Inside the WhiteBoardOS built-in terminal:

```bash
wb-full-test
```

Default standardized prompt:

> Find everything relevant to the MHacks WhiteBoardOS demo, explain how the system works, summarize what we should show judges, and name the most important files.

This runs the real integrated path: folder-aware semantic retrieval -> metadata/path/activity scoring -> graph expansion -> compact context -> Gemini 3.5 Flash-Lite -> optional raw reads or safe file actions.

The embedding model is not warmed on every invocation. WhiteBoardOS keeps it resident after boot; `wb-full-test` only warms and retries automatically if a query actually falls back.

## 4. Natural-language action example

```bash
wb-full-test "find all files related to my school classes and put them in one file called classes2 in the main folder"
```

Expected behavior:

1. `school classes` scopes retrieval toward `Classes/`.
2. Relevant class files are selected dynamically rather than using a fixed top-8.
3. Gemini calls WhiteBoardOS `consolidate_files` instead of merely describing the operation.
4. WhiteBoardOS creates `classes2.md` in the workspace root while preserving all source files.
5. SQLite is updated immediately; stale semantic state is invalidated; Qwen metadata, the embedding, and graph relationships refresh automatically in the background.

## 5. Persistent Gemini key

Set once:

```bash
wb gemini-key set
```

Check without displaying it:

```bash
wb gemini-key status
```

The key is stored at `/data/whiteboardos/.secrets/gemini_api_key` with mode `0600` and survives reboot and patch deployment.

## 6. Sync/debug commands

```bash
wb status
wb sync-status
wb retrieval-status
wb retrieve "school classes" --debug
```

Build identifier for this patch:

```text
0.12-action-router-v2.7
```

## Consistency rule

The real filesystem is the source of truth. SQLite, Qwen metadata, embeddings, and the graph are derived state.

- create/edit/import -> catalog updates immediately; old semantic state is invalidated; background Qwen + embedding + graph refresh follows
- rename/move -> stable file ID and embedding are preserved; path/search rows and graph relationships update
- delete/restore -> catalog and graph references are removed/restored and semantic state refreshes
- Gemini batch moves -> all filesystem mutations go through WhiteBoardOS APIs and graph rebuilds once per batch
- Gemini consolidation/create -> uses the same write path as the OS, so the resulting file is automatically indexed
- terminal/editor changes outside the UI -> filesystem signature polling notices the change and reconciles the derived state
