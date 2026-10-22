@echo off
chcp 65001 >nul
setlocal
title Reset sample data
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run  run-windows.bat  one time first.
  pause
  exit /b 1
)
echo.
echo This deletes everything inside the app and builds the sample data again.
echo Type yes and press Enter to continue:
set /p GO=
if /i not "%GO%"=="yes" (
  echo Cancelled.
  pause
  exit /b 0
)
".venv\Scripts\python.exe" reset_demo.py
echo.
echo Done. Now run run-windows.bat
pause
