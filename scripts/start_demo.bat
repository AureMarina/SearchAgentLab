@echo off
cd /d "%~dp0\.."
set PYTHONPATH=
where py >nul 2>nul
if %ERRORLEVEL%==0 (set PY=py -3) else (set PY=python)
%PY% examples\seed_studio_demo.py --data-dir ..\eval-data\demos\studio-demo
if ERRORLEVEL 1 exit /b 1
%PY% -m searchagent_lab serve --data-dir ..\eval-data\demos\studio-demo --port 8765 --open
pause
