@echo off
rem 0.6.0 live test (Phase 3 first batch: area mesh, joint patterns, area loads) in NEW scratch instances.
rem   run_live_phase3.bat [ETABS] [SAP2000] [SAFE] [--hidden]      Output: tests\results\live_phase3.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_phase3.py" %* > "%~dp0results\live_phase3.txt" 2>&1
