@echo off
rem Offline test-suite (fake CSI model, no program needed). Output: tests\results\offline.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" -m pytest "%~dp0." -q -p no:cacheprovider > "%~dp0results\offline.txt" 2>&1
