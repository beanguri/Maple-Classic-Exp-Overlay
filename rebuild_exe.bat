@echo off
rem Rebuild Maple Classic Exp Overlay.exe from this source tree, then offer to launch it.
rem Close any running overlay first so files can be replaced.
cd /d "%~dp0"

echo Stopping any running overlay...
taskkill /IM "Maple Classic Exp Overlay.exe" /F >nul 2>nul
taskkill /IM python.exe /F >nul 2>nul

echo Installing PyInstaller if needed...
py -3 -m pip install -q pyinstaller
if errorlevel 1 (
  echo pip/PyInstaller failed.
  pause
  exit /b 1
)

if exist "%~dp0tesseract\tesseract.exe" (
  set "TESSERACT_DIR=%~dp0tesseract"
) else if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
  set "TESSERACT_DIR=C:\Program Files\Tesseract-OCR"
) else (
  echo Tesseract not found. Put it in .\tesseract or install to Program Files.
  pause
  exit /b 1
)

echo Building with Tesseract from %TESSERACT_DIR% ...
py -3 build.py
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)

echo.
echo Ready: "%~dp0Maple Classic Exp Overlay.exe"
if /I "%~1"=="launch" goto launch
choice /C YN /M "Launch it now"
if errorlevel 2 goto done
:launch
start "" "%~dp0Maple Classic Exp Overlay.exe"
:done
