@echo off
rem 0.13.0 live test (Phase 5 program-specific modelling + Phase 6 design) in NEW scratch instances.
rem   run_live_phase56.bat [SAP2000] [ETABS] [SAFE] [--hidden]      Output: tests\results\live_phase56.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_phase56.py" %* > "%~dp0results\live_phase56.txt" 2>&1
