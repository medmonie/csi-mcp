@echo off
rem V2: rebuild CSI examples through the edit actions (copies read; rebuilt models saved under results).
rem   run_verify_v2_safe.bat (SAFE only)
rem   Output: tests\results\verify_v2.txt and tests\results\verify\v2\
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0verify\verify_v2.py" SAFE %* > "%~dp0results\verify_v2.txt" 2>&1
