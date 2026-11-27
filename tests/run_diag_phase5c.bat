@echo off
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0diag_phase5c.py" %* > "%~dp0results\diag_phase5c.txt" 2>&1
