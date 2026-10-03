#!/usr/bin/env bash
set -euo pipefail
images_dir=$1
board_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd "$board_dir/../../.." && pwd)
python3 "$project_dir/scripts/make-image.py" "$images_dir" "$HOST_DIR"
