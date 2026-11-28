@echo off
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0diag_811c.py" %* > "%~dp0results\diag_811c.txt" 2>&1
