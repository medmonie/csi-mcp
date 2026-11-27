@echo off
rem Diagnostic of the table display selection in a scratch instance. Output: tests\results\diag_tables.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0diag_safe_tables.py" %* > "%~dp0results\diag_tables.txt" 2>&1
