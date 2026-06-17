@echo off
setlocal

cd /d "%~dp0"
if errorlevel 1 (
    echo Failed to enter project directory.
    pause
    exit /b 1
)

if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    set "PYTHON_EXE=python"
)

echo Starting ZHX Research in developer mode...
echo URL: http://localhost:8501
echo Press Ctrl+C to stop the Streamlit service.
echo.

"%PYTHON_EXE%" -m streamlit run app.py --server.port 8501 --server.headless true

echo.
echo ZHX Research developer service exited.
pause
endlocal
