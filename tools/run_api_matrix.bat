@echo off
rem Phase 0: API capability matrix from the installed type libraries + runtime probe (starts each program hidden,
rem blank model, closes without saving). Writes docs\API_MATRIX.md and docs\api\. Log: tests\results\api_matrix.txt
if not exist "%~dp0..\tests\results" mkdir "%~dp0..\tests\results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0gen_api_matrix.py" %* > "%~dp0..\tests\results\api_matrix.txt" 2>&1
