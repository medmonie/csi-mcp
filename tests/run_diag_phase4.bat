@echo off
rem Diagnostic for the first Phase 4 live run (raw API).
rem   run_diag_phase4.bat [ETABS] [SAP2000] [SAFE] [--hidden]      Output: tests\results\diag_phase4.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0diag_phase4.py" %* > "%~dp0results\diag_phase4.txt" 2>&1
