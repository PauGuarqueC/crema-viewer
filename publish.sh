#!/bin/bash
# publish.sh — genera l'observacions_10d.json i la publica a GitHub Pages.
# Nomes cal un cop al dia (les dades XEMA es pengen a labfire un sol cop).
# La disponibilitat de plans es gestiona a part (publish_plans_status.sh),
# ja que la previsio de models canvia diverses vegades al dia i cal
# refrescar-la mes sovint.
set -euo pipefail

REPO_DIR="/home/pguarque/cremes_viewer"
PYTHON_BIN="/home/pguarque/graf_env/bin/python"

cd "$REPO_DIR"

# El formulari "+ Afegir PdC nou" (via Cloudflare Worker) pot fer commits
# directament a GitHub sense passar per aquest clonatge local -- cal
# sincronitzar abans de fer push, o el push seguent quedaria rebutjat
# (branques divergents) i tot l'script fallaria per el 'set -euo pipefail'.
git pull --no-rebase origin main --quiet || true

"$PYTHON_BIN" publish_xema_10d.py

git add data/observacions_10d.json
if ! git diff --cached --quiet; then
  git commit -m "Actualitza observacions XEMA ($(date -u +%Y-%m-%dT%H:%MZ))"
  git push origin main
else
  echo "Sense canvis a observacions_10d.json, no es fa push."
fi
