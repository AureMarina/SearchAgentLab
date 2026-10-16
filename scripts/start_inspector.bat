@echo off
cd /d "%~dp0\.."
set PYTHONPATH=
py -3 -m searchagent_lab inspect --data-dir ..\eval-data\demos\inspection --port 8765
pause
