#!/bin/bash
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
  echo "اول یک بار فایل run-mac.command را اجرا کن."
  read -n 1 -s -r -p "برای بستن، یک کلید بزن…"
  exit 1
fi
echo "این کار همه‌ی اطلاعات داخل برنامه را پاک می‌کند و داده‌ی نمونه را از نو می‌سازد."
read -r -p "برای ادامه بنویس  بله  و Enter بزن: " GO
if [ "$GO" != "بله" ]; then echo "انجام نشد."; read -n 1 -s -r -p "برای بستن، یک کلید بزن…"; exit 0; fi
".venv/bin/python" reset_demo.py
echo "تمام شد. حالا run-mac.command را اجرا کن."
read -n 1 -s -r -p "برای بستن، یک کلید بزن…"
