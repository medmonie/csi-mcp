@echo off
rem CSI MCP installer for ONE program (this package: the *-mcp-server.exe next to this file - etabs, sap2000 or
rem safe). Installs the server (a single exe, no Python needed) and adds it to Claude Desktop and Codex. The program
rem itself needs nothing installed: the server talks to its Open API directly. Installing removes the old combined
rem "csi" server (0.6.0 and earlier), which the three program servers replace.
setlocal
cd /d "%~dp0"
set "LOG=%~dp0install_result.txt"
echo CSI MCP install  %DATE% %TIME% > "%LOG%"
set "CLAUDE_OPEN="
for %%A in (%*) do if /i "%%~A"=="/claude_open" set "CLAUDE_OPEN=1"

set "EXE_NAME="
for %%F in (*-mcp-server.exe) do set "EXE_NAME=%%F"
if not defined EXE_NAME (
  echo The server exe ^(etabs-, sap2000- or safe-mcp-server.exe^) is missing next to install.bat. Unzip the whole package first.
  echo RESULT=FAILED: server exe missing >> "%LOG%"
  pause
  exit /b 1
)
set "NAME=%EXE_NAME:-mcp-server.exe=%"

rem Claude Desktop keeps its settings in memory and can write them back over the entry added here; it also holds the
rem server exe open. Quit it first (tray icon > Quit), then start it again after installing.
if not defined CLAUDE_OPEN tasklist /fi "imagename eq claude.exe" | find /i "claude.exe" >nul && (
  echo Claude is running. Quit Claude Desktop completely ^(tray icon ^> Quit^), then run install.bat again.
  echo ^(install.bat /claude_open installs anyway.^)
  echo RESULT=STOPPED: Claude Desktop running >> "%LOG%"
  pause
  exit /b 1
)
echo Installing the %NAME% server for %USERDOMAIN%\%USERNAME%  (%USERPROFILE%)
echo ACCOUNT %USERDOMAIN%\%USERNAME%  SERVER %NAME% >> "%LOG%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse -File | Unblock-File" >> "%LOG%" 2>&1

set "SRV=%LOCALAPPDATA%\CsiMcp\%NAME%"
if not exist "%SRV%" mkdir "%SRV%"
copy /y "%EXE_NAME%" "%SRV%\" >> "%LOG%" 2>&1 || (
  echo Could not copy the server to %SRV%. If an MCP client is running it, quit the client and try again.
  echo RESULT=FAILED: server copy >> "%LOG%"
  pause
  exit /b 1
)
set "EXE=%SRV%\%EXE_NAME%"
echo SERVER %EXE% >> "%LOG%"
> "%SRV%\claude_desktop_config_snippet.json" echo { "mcpServers": { "%NAME%": { "command": "%EXE:\=\\%" } } }
rem The combined server of 0.6.0 and earlier is replaced: remove its exe (its client entry is removed below).
if exist "%LOCALAPPDATA%\CsiMcp\csi-mcp-server.exe" del /q "%LOCALAPPDATA%\CsiMcp\csi-mcp-server.exe" "%LOCALAPPDATA%\CsiMcp\claude_desktop_config_snippet.json" >> "%LOG%" 2>&1

echo.
echo Connecting MCP clients:
set "CLIENTS=%TEMP%\csimcp_clients_%RANDOM%.txt"
"%EXE%" --configure-clients "%EXE%" > "%CLIENTS%" 2>&1
set "CRC=%ERRORLEVEL%"
type "%CLIENTS%"
type "%CLIENTS%" >> "%LOG%"
del /q "%CLIENTS%" 2>nul
echo CLIENTS exit %CRC% >> "%LOG%"
if not "%CRC%"=="0" echo Some client settings could not be changed. Add the %NAME% entry by hand: %SRV%\claude_desktop_config_snippet.json
echo RESULT=OK >> "%LOG%"
echo.
echo Installed:  %EXE%
echo Next:
echo   1. Start Claude Desktop again: it shows the "%NAME%" server.
echo   2. Open the program with a model and ask Claude for the model status.
echo.
echo This window closes in 10 seconds (press any key to close it now).
timeout /t 10 >nul
exit /b 0
