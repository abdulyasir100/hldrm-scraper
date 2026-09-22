@echo off
rem Double-click to open the Holodream Scraper window.
rem Uses this folder's own .venv when there is one, otherwise the Python on PATH -
rem whatever terminal or venv you happen to have open doesn't matter.
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "holodream_ui.pyw"
    exit /b 0
)
where pythonw >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3.10 or newer from python.org
    echo and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)
start "" pythonw "holodream_ui.pyw"
