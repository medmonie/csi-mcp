@echo off
rem Full edit / analysis / results cycle in NEW scratch instances (your open models are not touched).
rem   run_live_edit.bat [ETABS] [SAP2000] [SAFE] [--hidden]      Output: tests\results\live_edit.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_edit.py" %* > "%~dp0results\live_edit.txt" 2>&1
