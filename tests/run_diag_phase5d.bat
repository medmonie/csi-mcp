@echo off
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0diag_phase5d.py" %* > "%~dp0results\diag_phase5d.txt" 2>&1
