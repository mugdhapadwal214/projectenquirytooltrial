@echo off
REM ---------------------------------------------------------------------------
REM  Build the RM Estimator into a standalone Windows application.
REM  Run from this folder. No arguments.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

echo.
echo  RM Estimator - build
echo  ====================

REM --- 1. find a Python -------------------------------------------------------
call find_python.bat
if not defined PY (
  echo.
  echo  No Python found.
  echo.
  echo  The launcher reported "No suitable Python runtime found", which means
  echo  no version this script knows about is installed, or python.exe is not
  echo  on PATH.
  echo.
  echo  Install Python 3.12 from python.org, tick "Add python.exe to PATH"
  echo  during setup, then run this again.
  echo.
  pause
  exit /b 1
)

for /f "tokens=2" %%v in ('%PY% -V 2^>^&1') do set "PYVER=%%v"
echo.
echo  Using: %PY%   (Python %PYVER%)

REM  PyInstaller and pywebview trail new Python releases by months. The app
REM  itself runs fine; the tools that package it may not exist yet.
echo %PYVER% | findstr /b "3.14 3.15" >nul
if not errorlevel 1 (
  echo.
  echo  NOTE: Python %PYVER% is very new. PyInstaller and pywebview may not
  echo        have wheels for it yet. If the build fails below, install
  echo        Python 3.12 alongside it - both can coexist, and this script
  echo        will prefer 3.12 automatically.
)

REM --- 2. dependencies --------------------------------------------------------
echo.
echo  [1/4] Checking dependencies...
%PY% -c "import flask, docx, openpyxl" >nul 2>&1
if errorlevel 1 (
  echo        Installing app requirements...
  %PY% -m pip install -r requirements.txt
  if errorlevel 1 goto :failed
)
%PY% -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
  echo        Installing PyInstaller...
  %PY% -m pip install pyinstaller
  if errorlevel 1 goto :failed
)

REM --- 3. build ---------------------------------------------------------------
echo.
echo  [2/4] Building...
%PY% -m PyInstaller --noconfirm --clean rm_estimator.spec
if errorlevel 1 goto :failed

REM --- 4. editable data alongside the exe -------------------------------------
echo.
echo  [3/4] Copying editable data next to the exe...
REM  data\ must sit NEXT TO the exe, never inside it. PyInstaller unpacks
REM  bundled files to a temp folder that Windows wipes when the app closes -
REM  projects, offcut stock and job history would vanish every time.
xcopy /E /I /Y "data" "dist\RM Estimator\data" >nul
copy /Y "register_rmp.bat" "dist\RM Estimator\" >nul 2>&1
copy /Y "Material_Reference_for_review.xlsx" "dist\RM Estimator\" >nul 2>&1

echo.
echo  [4/4] Done.
echo.
echo      dist\RM Estimator\RM Estimator.exe
echo.
echo      To open .rmp files by double-clicking, go to that folder and run
echo      register_rmp.bat as a NORMAL user - not as administrator. Run it
echo      elevated and the association lands in the administrator's registry
echo      hive instead of yours, and nothing happens.
echo.
pause
exit /b 0

:failed
echo.
echo  BUILD FAILED - see the messages above.
echo.
echo  Most common causes:
echo    * PyInstaller has no wheel for Python %PYVER% yet
echo      ^-^> install Python 3.12 and run this again
echo    * A dependency failed to install
echo      ^-^> run:  %PY% -m pip install -r requirements.txt pyinstaller
echo.
pause
exit /b 1
