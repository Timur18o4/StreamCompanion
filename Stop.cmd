@echo off
cd /d "%~dp0"
python stop_tool.py
if errorlevel 1 pause
