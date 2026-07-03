@echo off
title Swim Coach AI
cd /d "%~dp0"
echo ============================================================
echo    Starting Swim Coach AI...
echo.
echo    A browser tab will open automatically at
echo        http://127.0.0.1:8765
echo.
echo    Use THAT tab. Keep this window open while you work.
echo    (Do not open swimlens.html directly - the key won't save.)
echo ============================================================
echo.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY ( where python3 >nul 2>nul && set "PY=python3" )
if not defined PY (
  echo  Could not start: Python 3 was not found.
  echo.
  echo   1^) Install Python 3 from https://www.python.org/downloads/
  echo   2^) During setup, TICK the box "Add Python to PATH"
  echo   3^) Then double-click "Swim Coach AI" again
  echo.
  pause
  exit /b
)
%PY% swimlens_server.py
echo.
echo ============================================================
echo  Swim Coach AI has stopped.
echo  If it closed due to an error, the details are shown above.
echo ============================================================
pause
