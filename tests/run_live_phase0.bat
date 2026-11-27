@echo off
rem 0.3.0 live test (Phase 0 + report R1) in NEW scratch instances; builds tests\reference_models\.
rem   run_live_phase0.bat [ETABS] [SAP2000] [SAFE] [--hidden]      Output: tests\results\live_phase0.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_phase0.py" %* > "%~dp0results\live_phase0.txt" 2>&1
