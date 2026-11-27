@echo off
rem 0.10.0 live test (RS / TH functions and cases) in NEW scratch instances.
rem   run_live_phase3d.bat [ETABS] [SAP2000] [SAFE] [--hidden]      Output: tests\results\live_phase3d.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_phase3d.py" %* > "%~dp0results\live_phase3d.txt" 2>&1
