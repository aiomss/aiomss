@echo off
setlocal
cd /d "%~dp0.."
title AI Ops Console - close this window to stop the service

echo ============================================================
echo   AI Ops Console - startup
echo ============================================================
echo.

set "ROOT=%CD%"
set "PY=%ROOT%\venv\Scripts\python.exe"
set "LOG=%ROOT%\logs\startup.log"
mkdir "%ROOT%\logs" >nul 2>nul
echo %DATE% %TIME% start >> "%LOG%" 2>nul

rem Make sure we are actually inside the project. Shortcut may be stale or folder moved.
if not exist "%ROOT%\backend\app.py" goto :BAD_LOCATION

rem If venv exists, verify it can actually start. Its base Python may have been removed.
if exist "%PY%" (
  "%PY%" -c "import sys" >nul 2>nul
  if errorlevel 1 (
    echo [WARN] Existing venv is broken. Base Python probably removed. Rebuilding...
    echo %DATE% %TIME% venv-broken-rebuild >> "%LOG%" 2>nul
    rmdir /s /q "%ROOT%\venv" >nul 2>nul
  ) else (
    goto :VENV_OK
  )
)

rem Create the venv from any available local Python 3.9 or newer. Never WorkBuddy managed Python.
echo [INFO] Creating or repairing project venv. First run installs deps, please wait...
set "CREATOR="
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "CREATOR=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined CREATOR if exist "C:\Python314\python.exe" set "CREATOR=C:\Python314\python.exe"
if not defined CREATOR set "CREATOR=python"
"%CREATOR%" -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul
if errorlevel 1 goto :NO_PYTHON
"%CREATOR%" -m venv "%ROOT%\venv" || goto :NO_PYTHON
"%PY%" -m pip install --upgrade pip >nul 2>&1

:VENV_OK
echo [INFO] Python: %PY%
echo.

rem Dependency check, with automatic repair. Reinstall if anything is missing.
echo [1/3] Checking Python dependencies...
"%PY%" -c "import fastapi, uvicorn, paramiko, PIL, requests, cryptography" >nul 2>nul
if errorlevel 1 (
  echo        Missing, installing now. Needs network.
  echo %DATE% %TIME% deps-missing-reinstall >> "%LOG%" 2>nul
  "%PY%" -m pip install -r "%ROOT%\requirements.txt"
  if errorlevel 1 goto :NO_DEPS
)
echo        OK
echo.

rem Port check.
echo [2/3] Checking port 127.0.0.1:8848...
"%PY%" -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); r=s.connect_ex(('127.0.0.1',8848)); s.close(); sys.exit(0 if r==0 else 1)" >nul 2>nul
if not errorlevel 1 goto :PORT_BUSY
echo        Port is free.
echo.

rem Start service.
echo [3/3] Starting service...
echo ------------------------------------------------------------
echo   URL        : http://127.0.0.1:8848
echo   Login page : http://127.0.0.1:8848/login
echo.
echo   Close this window to stop the service, or press Ctrl+C.
echo   The service exits automatically with this window.
echo ------------------------------------------------------------
echo.

start "" /min cmd /c "ping -n 4 127.0.0.1 >nul & start http://127.0.0.1:8848"

"%PY%" -m backend.app
set "EXITCODE=%ERRORLEVEL%"
echo.
echo ============================================================
echo   Service stopped. Exit code: %EXITCODE%
echo   If the code is not 0, read the error message above.
echo ============================================================
echo %DATE% %TIME% stopped exit=%EXITCODE% >> "%LOG%" 2>nul
echo.
if not "%EXITCODE%"=="0" timeout /t 10 >nul
exit /b %EXITCODE%

:BAD_LOCATION
echo.
echo [ERROR] Project files were not found at:
echo   %ROOT%
echo   backend\app.py is missing. The desktop shortcut may be stale,
echo   or the project folder was moved or renamed.
echo.
echo   Fix: re-create the desktop shortcut so it points to
echo     scripts\start.bat  inside the ai-ops-console folder.
echo.
echo %DATE% %TIME% BAD_LOCATION >> "%LOG%" 2>nul
timeout /t 10 >nul
exit /b 1

:NO_PYTHON
echo.
echo [ERROR] No usable Python 3.9 or newer was found on this machine.
echo   The console needs a local Python to build its virtual environment.
echo   Install Python 3.9 or newer from python.org. Check Add to PATH. Then retry.
echo   Or run once manually from the project root:
echo.
echo     "C:\Path\To\Python313\python.exe" -m venv .venv
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
echo %DATE% %TIME% NO_PYTHON >> "%LOG%" 2>nul
timeout /t 10 >nul
exit /b 1

:NO_DEPS
echo.
echo [ERROR] Required Python packages are missing and could not be installed.
echo   This usually means no network access during install.
echo   Fix. Run in the project root, with network:
echo.
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
echo %DATE% %TIME% NO_DEPS >> "%LOG%" 2>nul
timeout /t 10 >nul
exit /b 1

:PORT_BUSY
echo.
echo [INFO] Port 127.0.0.1:8848 is already in use.
echo        The service is probably already running.
echo        To stop it, close its console window.
echo        Opening the browser instead of starting a second instance.
start "" http://127.0.0.1:8848
echo %DATE% %TIME% PORT_BUSY >> "%LOG%" 2>nul
exit /b 0
