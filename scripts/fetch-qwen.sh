#!/usr/bin/env bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
model="$project_dir/assets/qwen2.5-0.5b-instruct-q4_k_m.gguf"
if [[ -e "$model" ]]; then echo 'Model already present; refusing to replace it.' >&2;exit 1;fi
curl --fail --location --retry 3 https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf -o "$model.partial"
mv "$model.partial" "$model"
sha256sum "$model" > "$model.sha256"
echo 'Qwen GGUF downloaded. WhiteBoardOS will register it with Ollama at first boot.'
