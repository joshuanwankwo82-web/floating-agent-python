@echo off
:: Friday -- one-click launcher for Windows.
:: 1) Installs the Python packages Friday needs (skips instantly if ready).
:: 2) Starts the floating circle (agent_overlay.py).
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   [MISSING] Python is not installed or not on PATH.
    echo   Get it from https://www.python.org/downloads/
    echo   IMPORTANT: tick "Add python.exe to PATH" during setup.
    echo.
    pause
    exit /b 1
)
echo Installing/checking dependencies (one-time startup, ~1-2 min first run)...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet "customtkinter>=5.2.2,<6" PySide6 pillow
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo.
    echo   [INFO] Some optional voice packages may have failed (PyAudio needs
    echo   a compiler on Windows) -- Friday still runs without them.
    echo.
)
echo.
echo Starting Friday...
python agent_overlay.py
exit /b 0
