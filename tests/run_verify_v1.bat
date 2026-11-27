@echo off
rem V1: the MCP's results against CSI's own verification examples (copies of the models; originals untouched).
rem   run_verify_v1.bat [SAP2000] [ETABS] [SAFE] [--hidden] [--only "Example 1-001"]
rem   Output: tests\results\verify_v1.txt and tests\results\verify\v1_results.csv
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0verify\verify_v1.py" %* > "%~dp0results\verify_v1.txt" 2>&1
