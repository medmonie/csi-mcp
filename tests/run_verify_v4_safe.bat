@echo off
rem V4 (SAFE): CSI slab design examples through the design actions.
rem   run_verify_v4_safe.bat [--hidden]
rem   Output: tests\results\verify_v4_safe.txt and tests\results\verify\v4_safe\
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0verify\verify_v4_safe.py" %* > "%~dp0results\verify_v4_safe.txt" 2>&1
