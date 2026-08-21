#!/bin/bash
# publish_plans_status.sh — recalcula la disponibilitat dels plans (verd/
# groc/vermell) i la publica. Pensat per correr diverses vegades al dia
# (a diferencia de publish.sh, que nomes cal un cop), ja que la previsio
# dels 4 models canvia amb cada tanda i el semafor es queda desactualitzat
# si nomes es calcula un cop.
set -euo pipefail

REPO_DIR="/home/pguarque/cremes_viewer"
PYTHON_BIN="/home/pguarque/graf_env/bin/python"

cd "$REPO_DIR"

# Per si algu altre ha fet push mentrestant (p.ex. publish.sh de bon mati,
# o edicions manuals de plans_llindars.json)
git pull --no-rebase origin main --quiet || true

"$PYTHON_BIN" compute_plans_status.py

git add data/plans_status.json
if ! git diff --cached --quiet; then
  git commit -m "Actualitza disponibilitat de plans ($(date -u +%Y-%m-%dT%H:%MZ))"
  git push origin main
else
  echo "Sense canvis a plans_status.json, no es fa push."
fi
