@echo off
rem Result diagrams vs the programs (multi-slab test models, SAFE RC-PN copy).
rem   run_live_diagrams.bat [SAP2000] [ETABS] [SAFE] [--hidden] [--keep-open]   Output: tests\results\live_diagrams.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_diagrams.py" %* > "%~dp0results\live_diagrams.txt" 2>&1
