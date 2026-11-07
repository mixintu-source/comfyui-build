@echo off
setlocal
cd /d "%~dp0"
title ComfyUI Model Migrator
"%~dp0runtime\python.exe" "%~dp0launcher.py" %*
if errorlevel 1 pause
