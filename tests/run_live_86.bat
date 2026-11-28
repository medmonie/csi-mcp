@echo off
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_86.py" %* > "%~dp0results\live_86.txt" 2>&1
