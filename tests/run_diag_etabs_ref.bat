@echo off
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0diag_etabs_ref.py" > "%~dp0results\diag_etabs_ref.txt" 2>&1
