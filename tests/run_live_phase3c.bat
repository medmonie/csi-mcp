@echo off
rem 0.9.0 live test (Phase 3 batch 3: objects) in NEW scratch instances.
rem   run_live_phase3c.bat [ETABS] [SAP2000] [SAFE] [--hidden]      Output: tests\results\live_phase3c.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_phase3c.py" %* > "%~dp0results\live_phase3c.txt" 2>&1
