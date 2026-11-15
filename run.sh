#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
  PY=".venv/Scripts/python.exe"
else
  if [ "${NO_INSTALL:-0}" = "1" ]; then
    echo "[KnowledgeLib] Missing .venv. Run: uv sync" >&2
    exit 1
  fi
  echo "[KnowledgeLib] .venv not found, running uv sync..."
  uv sync
  if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
  elif [ -x ".venv/Scripts/python.exe" ]; then
    PY=".venv/Scripts/python.exe"
  else
    echo "[KnowledgeLib] uv sync did not create .venv" >&2
    exit 1
  fi
fi
exec "$PY" app.py "$@"
