#!/bin/bash
cd "$(dirname "$0")"
PY=python3
if [ ! -x ".venv/bin/python" ]; then
  echo "بار اول است؛ دارم برنامه را آماده می‌کنم…"
  $PY -m venv .venv || exit 1
fi
if ! ".venv/bin/python" -c "import fastapi, uvicorn, sqlalchemy, openpyxl" >/dev/null 2>&1; then
  echo "دارم چیزهای لازم را از اینترنت می‌گیرم…"
  ".venv/bin/python" -m pip install --quiet --upgrade pip
  ".venv/bin/python" -m pip install --quiet -r requirements.txt
fi
exec ".venv/bin/python" tools_start.py
