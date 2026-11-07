@echo off
rem Windows may block .ps1 scripts; this wrapper runs it with a per-process bypass.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
