@echo off
cd /d "%~dp0"
python bootstrap.py --sender
if errorlevel 1 pause
