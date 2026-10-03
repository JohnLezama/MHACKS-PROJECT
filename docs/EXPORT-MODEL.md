# Optional: supply an existing Ollama model store

The GGUF route in START-HERE.md is simpler. Use this route if native Ollama is already installed on your laptop. The model weights/manifests can be exported across architectures; **the native Windows executable cannot be used in the Pi image**. You still need the separate Linux ARM64 runtime in assets/ollama-runtime.

On Windows in PowerShell from the extracted project directory, with Ollama running:

```powershell
ollama pull qwen2.5:0.5b-instruct-q4_K_M
ollama create whiteboard-metadata:latest -f .\models\Modelfile
py -3 scripts\export-model.py --source "$env:USERPROFILE\.ollama\models" --destination .\assets\ollama-models
```

Requires Python 3.11+ for the export helper. If OLLAMA_MODELS points to a custom directory, use that path as --source. The export copies only the manifest and its referenced blobs, verifies SHA256, and excludes unrelated models.

On Linux with a native Ollama installation:

```bash
ollama pull qwen2.5:0.5b-instruct-q4_K_M
ollama create whiteboard-metadata:latest -f models/Modelfile
python3 scripts/export-model.py --source ~/.ollama/models --destination assets/ollama-models
```

Copy the exported `assets/ollama-models` into the WSL/Linux build tree. Its `manifests/registry.ollama.ai/library/whiteboard-metadata/latest` and `blobs/sha256-*` are seeded into the data partition. This route avoids first-boot GGUF model registration. No network model download takes place on the Pi.
