@echo off
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0scripts\setup.py" --launch %*
    exit /b
)
where python >nul 2>nul
if errorlevel 1 (
    echo Install Python 3.10+ from python.org. See README.md. 1>&2
    exit /b 1
)
python "%~dp0scripts\setup.py" --launch %*
exit /b %errorlevel%
