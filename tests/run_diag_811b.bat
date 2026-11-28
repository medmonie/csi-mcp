@echo off
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0diag_811b.py" %* > "%~dp0results\diag_811b.txt" 2>&1
