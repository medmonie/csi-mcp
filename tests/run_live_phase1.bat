@echo off
rem 0.4.0 live test (Phase 1 model reading + report R2 diagrams) in NEW scratch instances.
rem   run_live_phase1.bat [ETABS] [SAP2000] [SAFE] [--hidden]      Output: tests\results\live_phase1.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_phase1.py" %* > "%~dp0results\live_phase1.txt" 2>&1
