#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
unset PYTHONPATH
"${PYTHON:-python3}" -m venv .venv
.venv/bin/python -m pip install --no-index --no-deps dist/searchagent_lab-0.9.9-py3-none-any.whl
printf '\nInstalled in %s/.venv. See README_ZH.md to start a synthetic demo or your real workspace.\n' "$ROOT"
