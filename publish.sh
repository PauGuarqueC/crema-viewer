#!/bin/bash
# publish.sh — genera l'observacions_10d.json, calcula la disponibilitat
# dels plans, i publica tot a GitHub Pages.
set -euo pipefail

REPO_DIR="/home/pguarque/cremes_viewer"
PYTHON_BIN="/home/pguarque/graf_env/bin/python"

cd "$REPO_DIR"

# 1. Observacions dels darrers 10 dies
"$PYTHON_BIN" publish_xema_10d.py

# 2. Disponibilitat dels plans (necessita el fitxer del pas 1, ja al disc local)
"$PYTHON_BIN" compute_plans_status.py

git add data/observacions_10d.json data/plans_status.json
if ! git diff --cached --quiet; then
  git commit -m "Actualitza observacions XEMA + disponibilitat plans ($(date -u +%Y-%m-%dT%H:%MZ))"
  git push origin main
else
  echo "Sense canvis, no es fa push."
fi
