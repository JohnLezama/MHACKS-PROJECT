#!/usr/bin/env bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
archive="$project_dir/assets/ollama-linux-arm64.tar.zst"
if [[ -e "$project_dir/assets/ollama-runtime/bin/ollama" ]]; then echo 'Runtime already present; refusing to replace it.' >&2;exit 1;fi
curl --fail --location --retry 3 https://ollama.com/download/ollama-linux-arm64.tar.zst -o "$archive.partial"
mv "$archive.partial" "$archive"
# Preserve the archive structure: bin/ollama and lib/ollama belong together.
tar --zstd -xf "$archive" -C "$project_dir/assets/ollama-runtime"
sha256sum "$archive" > "$archive.sha256"
echo 'ARM64 runtime downloaded. Do not run it on an x86 laptop; it is for the image.'
