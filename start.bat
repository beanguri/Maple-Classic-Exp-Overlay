@echo off
rem Starts Maple Classic Exp Overlay from the source code.
rem   start.bat          starts it without a console window
rem   start.bat debug    starts it with a console window (shows errors while you work on the code)
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY ( where python >nul 2>nul && set PY=python )
if not defined PY (
    echo Python was not found. Install Python 3.10 or newer from python.org ^(tick "Add to PATH"^) and run this again.
    pause
    exit /b 1
)

%PY% -c "import cv2, mss, numpy, PIL" >nul 2>nul || (
    echo Installing the required packages ^(first run only^)...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Installing the packages failed. See above.
        pause
        exit /b 1
    )
)

if not exist "C:\Program Files\Tesseract-OCR\tesseract.exe" if not exist "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe" if not exist "%~dp0tesseract\tesseract.exe" (
    echo Note: Tesseract OCR was not found, so map names will not be read.
    echo Install it from https://github.com/UB-Mannheim/tesseract/wiki ^(default folder^), then start again.
    echo.
    timeout /t 5 >nul
)

if /i "%~1"=="debug" (
    %PY% maple_exp.py
    if errorlevel 1 (
        echo.
        echo The program stopped with an error. See above.
        pause
    )
    exit /b
)

rem No console: pythonw (next to python.exe) runs the same program without a window.
set PYW=
for /f "delims=" %%i in ('%PY% -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set PYW=%%i
if exist "%PYW%" (
    start "" "%PYW%" maple_exp.py
) else (
    start "" %PY% maple_exp.py
)
