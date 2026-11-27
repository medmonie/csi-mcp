@echo off
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0diag_safe_ref.py" > "%~dp0results\diag_safe_ref.txt" 2>&1
