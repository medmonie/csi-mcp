@echo off
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_sap5c.py" %* > "%~dp0results\live_sap5c.txt" 2>&1
