@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0za_extractor.py"
) else (
    python "%~dp0za_extractor.py"
    if errorlevel 1 pause
)
