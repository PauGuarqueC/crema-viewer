#!/bin/bash
# publish_backfill.sh — completa les dades observades de les cremes marcades
# (compute_backfill_cremes.py) i PUBLICA el resultat. Abans l'script python
# modificava data/cremes_realitzades.json sense commit, cosa que bloquejava
# el git pull dels altres scripts.
set -euo pipefail

REPO_DIR="/home/pguarque/cremes_viewer"
PYTHON_BIN="/home/pguarque/graf_env/bin/python"

cd "$REPO_DIR"
git pull --no-rebase origin main --quiet || { echo "ERROR: git pull ha fallat" >&2; exit 1; }

"$PYTHON_BIN" compute_backfill_cremes.py

git add data/cremes_realitzades.json
if ! git diff --cached --quiet; then
  git commit -m "Completa observat de cremes realitzades ($(date -u +%Y-%m-%dT%H:%MZ))"
  git push origin main
else
  echo "Sense canvis a cremes_realitzades.json, no es fa push."
fi
