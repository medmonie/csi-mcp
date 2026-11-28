@echo off
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_props85.py" %* > "%~dp0results\live_props85.txt" 2>&1
