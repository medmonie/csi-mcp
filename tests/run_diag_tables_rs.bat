@echo off
rem Diagnostic: RS / TH function and case tables (scratch hidden instances). Output: tests\results\diag_tables_rs.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0diag_tables_rs.py" %* > "%~dp0results\diag_tables_rs.txt" 2>&1
