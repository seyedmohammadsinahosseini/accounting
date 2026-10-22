#!/usr/bin/env bash
# اجرای برنامه روی سرور
set -euo pipefail
cd "$(dirname "$0")"
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
