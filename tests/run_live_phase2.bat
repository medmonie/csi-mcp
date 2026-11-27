@echo off
rem 0.5.0 live test (Phase 2 results + report R3 result diagrams) in NEW scratch instances.
rem   run_live_phase2.bat [ETABS] [SAP2000] [SAFE] [--hidden]      Output: tests\results\live_phase2.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_phase2.py" %* > "%~dp0results\live_phase2.txt" 2>&1
