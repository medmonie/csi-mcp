@echo off
rem V1 step 1: inventory of the CSI verification models (copies only, no analysis).
rem   run_verify_inventory.bat [SAP2000] [ETABS] [SAFE] [--hidden]      Output: tests\results\verify_inventory.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0verify\inventory.py" %* > "%~dp0results\verify_inventory.txt" 2>&1
