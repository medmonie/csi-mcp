@echo off
rem Read actions against the programs that are open (open a model first; nothing is changed).
rem   run_live_read.bat [ETABS] [SAP2000] [SAFE]      Output: tests\results\live_read.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_read.py" %* > "%~dp0results\live_read.txt" 2>&1
