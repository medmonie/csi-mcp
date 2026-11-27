@echo off
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_wind.py" %* > "%~dp0results\live_wind.txt" 2>&1
