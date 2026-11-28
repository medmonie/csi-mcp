@echo off
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_811.py" %* > "%~dp0results\live_811.txt" 2>&1
