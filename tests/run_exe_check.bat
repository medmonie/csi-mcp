@echo off
rem Starts each packaged edition over stdio and checks the protocol. Output: tests\results\exe_check.txt
rem   run_exe_check.bat [path to one exe]    default: the three editions in dist\
if not exist "%~dp0results" mkdir "%~dp0results"
set "OUT=%~dp0results\exe_check.txt"
set "PY=%~dp0..\server\.venv\Scripts\python"
if not "%~1"=="" (
  "%PY%" "%~dp0protocol_check.py" "%~1" > "%OUT%" 2>&1
  exit /b
)
echo exe check %DATE% %TIME% > "%OUT%"
set "FAIL="
for %%E in ("EtabsMcp\etabs" "Sap2000Mcp\sap2000" "SafeMcp\safe") do (
  echo ################ %%~E-mcp-server.exe >> "%OUT%"
  "%PY%" "%~dp0protocol_check.py" "%~dp0..\dist\%%~E-mcp-server.exe" >> "%OUT%" 2>&1 || set "FAIL=1"
)
if defined FAIL (echo ALL=FAILED >> "%OUT%") else (echo ALL=OK >> "%OUT%")
