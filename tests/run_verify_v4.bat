@echo off
rem V4: CSI design examples through the design actions.
rem   run_verify_v4.bat [--hidden]
rem   Output: tests\results\verify_v4.txt and tests\results\verify\v4\
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0verify\verify_v4.py" %* > "%~dp0results\verify_v4.txt" 2>&1
