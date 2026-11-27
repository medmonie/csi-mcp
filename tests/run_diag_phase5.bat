@echo off
rem Phase 5 diagnostic (raw API): auto seismic ASCE 7-16 call variants, SAP2000 design table names.
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0diag_phase5.py" %* > "%~dp0results\diag_phase5.txt" 2>&1
