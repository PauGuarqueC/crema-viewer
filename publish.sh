#!/bin/bash
# publish.sh — genera l'observacions_10d.json i el publica a GitHub Pages.
# Pensat per cron a labfire.ctfc.cat, mateix patró que echotops-viewer.
set -euo pipefail

REPO_DIR="/home/labfire/crema-viewer"
CONDA_ENV="graf_env"

cd "$REPO_DIR"

source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV"

python publish_xema_10d.py

git add data/observacions_10d.json
if ! git diff --cached --quiet; then
  git commit -m "Actualitza observacions XEMA ($(date -u +%Y-%m-%dT%H:%MZ))"
  git push origin main
else
  echo "Sense canvis a observacions_10d.json, no es fa push."
fi
