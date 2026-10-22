#!/bin/bash
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "پایتون روی این کامپیوتر نصب نیست. از python.org نصبش کن و بعد دوباره این فایل را اجرا کن."
  open "https://www.python.org/downloads/"
  read -n 1 -s -r -p "برای بستن، یک کلید بزن…"
  exit 1
fi
if [ ! -x ".venv/bin/python" ]; then
  echo "بار اول است؛ دارم برنامه را آماده می‌کنم. چند دقیقه طول می‌کشد…"
  python3 -m venv .venv
fi
if ! ".venv/bin/python" -c "import fastapi, uvicorn, sqlalchemy, openpyxl" >/dev/null 2>&1; then
  echo "دارم چیزهای لازم را از اینترنت می‌گیرم…"
  ".venv/bin/python" -m pip install --quiet --upgrade pip
  ".venv/bin/python" -m pip install --quiet -r requirements.txt
fi
".venv/bin/python" tools_start.py
read -n 1 -s -r -p "برای بستن، یک کلید بزن…"
