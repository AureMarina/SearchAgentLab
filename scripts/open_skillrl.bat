@echo off
cd /d "%~dp0\.."
python -m searchagent_lab methods ..\eval-data\reference-assets\skillrl\SkillRL_skills_20261005.zip --data-dir ..\eval-data\demos\skillrl-view --open %*
