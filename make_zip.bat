@echo off
setlocal
title PerfDog-CN - make share zip
cd /d "%~dp0"

echo ==================================================
echo  PerfDog-CN  : 打包分发 zip（替代 exe / 安装包）
echo ==================================================
echo.

rem --- prefer uv (auto-provisions Python); fall back to system python ---
where uv >nul 2>nul
if not errorlevel 1 (
  uv run --no-project python tools\make_zip.py %*
  goto :done
)

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] 需要 uv 或 python：
  echo         pip install uv      ^(或^)  https://docs.astral.sh/uv/
  pause
  exit /b 1
)

python tools\make_zip.py %*

:done
echo.
pause
endlocal
