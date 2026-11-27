@echo off
rem 0.14.0 live test (Phase 5 second batch) in NEW scratch instances.
rem   run_live_phase5b.bat [SAP2000] [ETABS] [--hidden]      Output: tests\results\live_phase5b.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_phase5b.py" %* > "%~dp0results\live_phase5b.txt" 2>&1
