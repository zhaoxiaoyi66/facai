@echo off
setlocal

cd /d C:\dev\facai
if errorlevel 1 (
    echo Failed to enter C:\dev\facai
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Python virtual environment not found: C:\dev\facai\.venv\Scripts\python.exe
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "scripts\launch_zhx_research.py"
if errorlevel 1 (
    echo.
    echo ZHX Research launcher failed.
    pause
    exit /b 1
)

endlocal
