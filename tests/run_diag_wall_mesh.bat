@echo off
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0diag_wall_mesh.py" %* > "%~dp0results\diag_wall_mesh.txt" 2>&1
