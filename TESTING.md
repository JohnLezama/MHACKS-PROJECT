# Validation record

The authoring environment blocked Buildroot's source download (HTTP 403) and had no physical Pi. The delivered ZIP is source only, not a compiled image.

Performed locally:

- 32 existing tests for core files, crash recovery, agent protocol/checkpoints, metadata caching and validation, activity/timezone retrieval, PDF extraction, HTTP handler host/token checks and UI contract.
- 5 new tests of asset architecture rejection, manifest checksum integrity, model export and network setting generation. Total: **37 passing tests**.
- Python compilation, shell syntax checks and JavaScript syntax check.
- The asset checker correctly refuses to build with intentionally absent runtime/model/settings.

Not performed: Buildroot Kconfig/package compilation, actual image generation with genimage, ARM64 runtime linking/execution, Wi-Fi/DHCP, SSH tunneling, real local inference/Gemini API calls, visual browser interaction, SD flashing or physical Pi boot. Some hardware/package integration fixes may be needed on the first actual build.

Run tests with Python 3.11+:

```bash
python3 -m unittest discover -s tests -v
bash -n scripts/*.sh buildroot-external/board/pi4/*.sh
node --check ui/app.js
python3 scripts/check-assets.py --allow-missing
```

Node is only needed for the optional JavaScript syntax check, not for the build or Pi runtime. Before compiling, use `python3 scripts/check-assets.py` without --allow-missing. After boot, use `whiteboard-check` and the physical acceptance checklist in START-HERE.md.
