@echo off
setlocal
title PerfCollect-CN Dashboard
cd /d "%~dp0collector"

set "UV=uv"
where uv >nul 2>nul
if errorlevel 1 (
  echo [ERROR] uv not found in PATH.
  echo         Install uv:  pip install uv   or   https://docs.astral.sh/uv/
  echo         Or use system Python directly:  python dashboard.py
  pause
  exit /b 1
)

start "" http://localhost:8080/report.html

echo.
echo ==================================================
echo  PerfCollect-CN  : dashboard only (no device needed)
echo  History     : http://localhost:8080/report.html
echo  Stop        : close window or Ctrl+C
echo ==================================================
echo.
rem --- --with openpyxl: only "export XLSX" needs it (keeps zip distribution zero-setup) ---
"%UV%" run --no-project --with openpyxl python dashboard.py

pause
endlocal
