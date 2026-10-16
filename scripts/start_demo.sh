#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
unset PYTHONPATH
PY="${PYTHON:-python3}"
"$PY" examples/seed_studio_demo.py --data-dir "$ROOT/../eval-data/demos/studio-demo"
exec "$PY" -m searchagent_lab serve --data-dir "$ROOT/../eval-data/demos/studio-demo" --port "${PORT:-8765}" --open
