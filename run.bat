@echo off
REM ---------------------------------------------------------------------------
REM  Run the RM Estimator from source (no build needed).
REM  Optionally pass a .rmp file to open:   run.bat "C:\path\MS_001.rmp"
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

call find_python.bat
if not defined PY (
  echo.
  echo  No Python found. Install Python 3.12 from python.org and tick
  echo  "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b 1
)

%PY% -c "import flask" >nul 2>&1
if errorlevel 1 (
  echo  Installing requirements...
  %PY% -m pip install -r requirements.txt
)

%PY% app.py %*
if errorlevel 1 (
  echo.
  echo  Failed to start. Try:  %PY% -m pip install -r requirements.txt
  echo  Or run in a browser:   %PY% app.py --web
  pause
)
