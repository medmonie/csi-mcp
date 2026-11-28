@echo off
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0diag_86c.py" %* > "%~dp0results\diag_86c.txt" 2>&1
