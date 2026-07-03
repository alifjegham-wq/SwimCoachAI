@echo off
title Install AI Tracking (one-time)
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo Python 3 was not found.
  echo Install it from https://www.python.org/downloads/ and tick "Add Python to PATH",
  echo then run this again.
  echo.
  pause
  exit /b
)
echo ============================================================
echo  Installing robust swimmer tracking ^(YOLO + BoT-SORT^).
echo  This downloads several HUNDRED MB ^(PyTorch + model weights^).
echo  It only needs to be done ONCE. Keep this window open.
echo ============================================================
echo.
%PY% -m pip install --upgrade pip
echo Installing CPU PyTorch (no GPU needed)...
%PY% -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
echo Installing tracking packages (pinned)...
%PY% -m pip install "ultralytics>=8.2,<8.4" "opencv-python>=4.8"
echo.
echo Pre-downloading the pose model (yolo11l-pose - detection + keypoints)...
%PY% -c "from ultralytics import YOLO; YOLO('yolo11l-pose.pt'); print('Model ready.')"
echo.
echo ============================================================
echo  Done. You can close this window.
echo  In Swim Coach AI, "Robust AI tracking (helper)" will now work.
echo ============================================================
pause
