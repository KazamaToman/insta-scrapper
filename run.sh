#!/usr/bin/env bash
# Convenience launcher: sets up a venv with the web extras, then serves the UI.
#
#   ./run.sh                 # http://127.0.0.1:8000
#   ./run.sh --port 9000     # custom port
#   PORT=9000 ./run.sh
#
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
VENV=".venv"

if [ ! -d "$VENV" ]; then
  echo "→ creating virtualenv in $VENV"
  "$PYTHON" -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

if ! python -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  echo "→ installing web dependencies"
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
fi

echo "→ starting instascrapper web UI"
exec python -m instascrapper serve --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" "$@"
