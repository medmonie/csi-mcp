@echo off
rem 0.8.0 live test (Phase 3 batch 2: definitions) in NEW scratch instances.
rem   run_live_phase3b.bat [ETABS] [SAP2000] [SAFE] [--hidden]      Output: tests\results\live_phase3b.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_phase3b.py" %* > "%~dp0results\live_phase3b.txt" 2>&1
