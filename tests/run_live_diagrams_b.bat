@echo off
rem Same as run_live_diagrams.bat, written to tests\results\diagrams_output_b (when models in diagrams_output are open).
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0live_diagrams.py" --out=diagrams_output_b %* > "%~dp0results\live_diagrams_b.txt" 2>&1
