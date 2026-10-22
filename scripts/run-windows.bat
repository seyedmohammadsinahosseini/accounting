@echo off
chcp 65001 >nul
setlocal
title Accounting App
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "LOG=run-log.txt"
echo === Accounting app - run log === > "%LOG%"
echo Date: %DATE% %TIME% >> "%LOG%"
echo Folder: %CD% >> "%LOG%"

set "PY="
set "PYARG="
call :tryname python
call :tryname python3
call :tryname py -3
if not defined PY call :scandirs
if not defined PY goto :nopython

echo Found: %PY% %PYARG% >> "%LOG%"
%PY% %PYARG% -c "import sys; print(sys.version)" >> "%LOG%" 2>&1
echo Using Python: %PY% %PYARG%
echo.

if not exist ".venv\Scripts\python.exe" (
  echo Preparing the app for the first time. Please wait...
  %PY% %PYARG% -m venv .venv >> "%LOG%" 2>&1
  if not exist ".venv\Scripts\python.exe" goto :venvfail
)

set "VENVPY=.venv\Scripts\python.exe"
"%VENVPY%" -c "import fastapi, uvicorn, sqlalchemy, openpyxl" >nul 2>nul
if errorlevel 1 (
  echo Getting the required parts from the internet. Please wait...
  "%VENVPY%" -m pip install --quiet --upgrade pip >> "%LOG%" 2>&1
  "%VENVPY%" -m pip install --quiet -r requirements.txt >> "%LOG%" 2>&1
  "%VENVPY%" -c "import fastapi, uvicorn, sqlalchemy, openpyxl" >nul 2>nul
  if errorlevel 1 goto :pipfail
)

"%VENVPY%" tools_start.py
echo.
echo The app has stopped. You can close this window.
pause
exit /b 0


:tryname
if defined PY exit /b
%~1 %~2 -c "import sys" >nul 2>nul
if errorlevel 1 exit /b
set PY="%~1"
set PYARG=%~2
exit /b

:scandirs
for /d %%P in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :trypath "%%~fP\python.exe"
for /d %%P in ("%ProgramFiles%\Python3*") do call :trypath "%%~fP\python.exe"
for /d %%P in ("C:\Python3*") do call :trypath "%%~fP\python.exe"
call :trypath "%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe"
call :trypath "C:\ProgramData\Anaconda3\python.exe"
call :trypath "%USERPROFILE%\Anaconda3\python.exe"
call :trypath "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"
exit /b

:trypath
if defined PY exit /b
if not exist %1 exit /b
%1 -c "import sys" >nul 2>nul
if errorlevel 1 exit /b
set PY=%1
exit /b


:nopython
echo.
echo Python was not found on this computer.
echo A help page is opening. Please follow it and then run this file again.
echo --- python not found --- >> "%LOG%"
where python >> "%LOG%" 2>&1
where py >> "%LOG%" 2>&1
where python3 >> "%LOG%" 2>&1
echo PATH=%PATH% >> "%LOG%"
start "" "install-python-help.html"
pause
exit /b 1

:venvfail
echo.
echo Something went wrong while preparing the app.
echo Please send the file  run-log.txt  to me.
pause
exit /b 2

:pipfail
echo.
echo The required parts could not be downloaded.
echo Please check the internet connection and run this file again.
pause
exit /b 3
