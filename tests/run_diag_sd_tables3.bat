@echo off
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0diag_sd_tables3.py" %* > "%~dp0results\diag_sd_tables3.txt" 2>&1
