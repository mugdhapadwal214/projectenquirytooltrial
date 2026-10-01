@echo off
REM ---------------------------------------------------------------------------
REM  find_python.bat  -  locate a usable Python and leave it in %PY%
REM
REM  Called by the other scripts with:  call find_python.bat
REM  Sets PY (e.g. "py -3.12" or "python") or leaves it empty if none found.
REM
REM  Why this exists: the build script used to hardcode "py -3.11". On a machine
REM  without 3.11 the launcher answers "No suitable Python runtime found" and
REM  the build stops before it starts. Version preference belongs in one place,
REM  and it should degrade to whatever is actually installed.
REM
REM  Order is deliberate. 3.12 and 3.11 first because PyInstaller and pywebview
REM  have shipped wheels for them the longest. Very new versions often work for
REM  the app itself but not yet for the tools that package it.
REM ---------------------------------------------------------------------------
set "PY="
call :try py -3.12
call :try py -3.11
call :try py -3.13
call :try py -3.10
call :try py -3
call :try python
exit /b 0

:try
if defined PY exit /b 0
%* -c "import sys" >nul 2>&1
if errorlevel 1 exit /b 0
set "PY=%*"
exit /b 0
