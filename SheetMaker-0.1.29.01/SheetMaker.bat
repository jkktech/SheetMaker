@echo off
cd /d "%~dp0"
py sheetmaker.py %*
if errorlevel 1 pause
