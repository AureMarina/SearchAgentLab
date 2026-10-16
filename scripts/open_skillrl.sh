#!/usr/bin/env sh
# Use the current Python environment; creates a separate local viewing workspace.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
exec "${PYTHON:-python3}" -m searchagent_lab methods \
  ../eval-data/reference-assets/skillrl/SkillRL_skills_20261005.zip \
  --data-dir ../eval-data/demos/skillrl-view --open "$@"
