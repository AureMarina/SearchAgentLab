#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# Avoid an exported 0.2.0 PYTHONPATH shadowing the new source package.
unset PYTHONPATH
exec "${PYTHON:-python3}" -m searchagent_lab inspect --data-dir "${1:-../eval-data/demos/inspection}" --port "${PORT:-8765}"
