@echo off
rem One-time setup of the MCP server: creates server\.venv, installs the packages and writes the blocks to add to
rem Claude's / Codex's configuration (claude_config_snippet.json, codex_config_snippet.toml). Log: setup_result.txt
rem Needs 64-bit Python 3.10+ and internet. Run it again after pulling a new version.
setlocal
cd /d "%~dp0"
set "LOG=%~dp0setup_result.txt"
echo CSI MCP server setup  %DATE% %TIME% > "%LOG%"

if exist "server\.venv\Scripts\python.exe" goto :install
py -3 -m venv "server\.venv" >> "%LOG%" 2>&1
if exist "server\.venv\Scripts\python.exe" goto :install
python -m venv "server\.venv" >> "%LOG%" 2>&1
if exist "server\.venv\Scripts\python.exe" goto :install
for /d %%P in ("%LOCALAPPDATA%\Python\pythoncore-*") do if not exist "server\.venv\Scripts\python.exe" "%%P\python.exe" -m venv "server\.venv" >> "%LOG%" 2>&1
if exist "server\.venv\Scripts\python.exe" goto :install
echo RESULT=FAILED: Python was not found >> "%LOG%"
echo Python was not found. Install 64-bit Python 3.10 or later, then run this again.
pause
exit /b 1

:install
"server\.venv\Scripts\python.exe" -c "import struct,sys; print('python', sys.version); sys.exit(0 if struct.calcsize('P')==8 else 1)" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo RESULT=FAILED: 32-bit Python - CSI programs need 64-bit >> "%LOG%"
  echo server\.venv uses 32-bit Python. Delete server\.venv, install 64-bit Python, run this again.
  pause
  exit /b 1
)
"server\.venv\Scripts\python.exe" -m pip install --upgrade pip >> "%LOG%" 2>&1
"server\.venv\Scripts\python.exe" -m pip install -r "server\requirements.txt" pytest >> "%LOG%" 2>&1
if errorlevel 1 (
  echo RESULT=FAILED: pip install >> "%LOG%"
  echo pip install failed, see setup_result.txt
  pause
  exit /b 1
)
rem Generate the comtypes wrappers for the CSI type libraries now (not during the first Claude call).
"server\.venv\Scripts\python.exe" "tools\gen_typelibs.py" >> "%LOG%" 2>&1

set "SNIP=%CD%\claude_config_snippet.json"
"server\.venv\Scripts\python.exe" "tools\write_client_configs.py" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo RESULT=FAILED: configuration generation >> "%LOG%"
  echo Configuration generation failed. See setup_result.txt
  pause
  exit /b 1
)
type "%SNIP%" >> "%LOG%"
echo RESULT=OK >> "%LOG%"
echo.
echo Server ready (development). The entries "etabs", "sap2000" and "safe" of this file run this checkout; add the
echo ones you need to Claude's configuration, next to "civil3d" and "revit", and remove an old "csi" entry:
echo   %SNIP%
echo (Claude desktop: Settings ^> Developer ^> Edit Config), then restart Claude completely.
echo Or let one be added for you:  server\.venv\Scripts\python server\server.py --program ETABS --configure-clients
echo The packaged servers (dist\EtabsMcp etc., build_package.bat) are the normal way to install.
echo For local Codex, merge codex_config_snippet.toml into your Codex config.toml.
echo.
type "%SNIP%"
if /i not "%~1"=="/nopause" timeout /t 20
