@echo off
rem 0.12.0 live test (Phase 4: new model, spec, tables, geometry) in NEW scratch instances.
rem   run_live_phase4.bat [ETABS] [SAP2000] [SAFE] [--hidden]      Output: tests\results\live_phase4.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_phase4.py" %* > "%~dp0results\live_phase4.txt" 2>&1
