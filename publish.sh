#!/bin/bash
# publish.sh — genera l'observacions_10d.json i el publica a GitHub Pages.
set -euo pipefail

REPO_DIR="/home/pguarque/cremes_viewer"
PYTHON_BIN="/home/pguarque/graf_env/bin/python"

cd "$REPO_DIR"

"$PYTHON_BIN" publish_xema_10d.py

git add data/observacions_10d.json
if ! git diff --cached --quiet; then
  git commit -m "Actualitza observacions XEMA ($(date -u +%Y-%m-%dT%H:%MZ))"
  git push origin main
else
  echo "Sense canvis a observacions_10d.json, no es fa push."
fi
