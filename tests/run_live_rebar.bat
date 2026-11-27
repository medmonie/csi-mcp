@echo off
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_rebar.py" %* > "%~dp0results\live_rebar.txt" 2>&1
