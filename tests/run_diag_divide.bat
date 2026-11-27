@echo off
rem Diagnostic: EditArea.Divide call forms (SAP2000, hidden scratch instance). Output: tests\results\diag_divide.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0diag_divide.py" > "%~dp0results\diag_divide.txt" 2>&1
