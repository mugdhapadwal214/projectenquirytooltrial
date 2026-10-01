@echo off
REM ---------------------------------------------------------------------------
REM  Associate .rmp project files with this app, so double-clicking one opens it.
REM
REM  RUN AS A NORMAL USER, NOT AS ADMINISTRATOR.
REM  These keys go under HKEY_CURRENT_USER. Running elevated writes them to the
REM  administrator's hive instead of yours, and nothing happens when you
REM  double-click a file.
REM ---------------------------------------------------------------------------
setlocal
set "APP=%~dp0RM Estimator.exe"

if not exist "%APP%" (
  echo  Could not find "%APP%".
  echo  Run this from the same folder as the exe, after building.
  pause
  exit /b 1
)

reg add "HKCU\Software\Classes\.rmp" /ve /d "RMEstimator.Project" /f >nul
reg add "HKCU\Software\Classes\RMEstimator.Project" /ve /d "RM Estimator Project" /f >nul
reg add "HKCU\Software\Classes\RMEstimator.Project\shell\open\command" /ve ^
    /d "\"%APP%\" \"%%1\"" /f >nul

echo.
echo  .rmp files are now associated with:
echo     %APP%
echo.
echo  Double-click any .rmp to open it in the estimator.
echo.
pause
