@echo off
cd /d "%~dp0"
uv run python -m spectra --open %*
if errorlevel 1 pause
