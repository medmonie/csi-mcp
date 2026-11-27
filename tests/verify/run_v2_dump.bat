@echo off
rem V2 step 1: read the V2 examples (copies). Output: tests\results\verify\v2\ and tests\results\v2_dump.txt
if not exist "%~dp0..\results" mkdir "%~dp0..\results"
"%~dp0..\..\server\.venv\Scripts\python" "%~dp0v2_dump.py" %* > "%~dp0..\results\v2_dump.txt" 2>&1
