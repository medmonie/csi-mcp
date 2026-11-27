@echo off
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0diag_wind.py" %* > "%~dp0results\diag_wind.txt" 2>&1
