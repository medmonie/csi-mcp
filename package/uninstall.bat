@echo off
rem Removes this package's server (etabs, sap2000 or safe) and its entry in Claude Desktop and Codex (other servers
rem and settings are kept; each file is backed up first).
setlocal
cd /d "%~dp0"
set "EXE_NAME="
for %%F in (*-mcp-server.exe) do set "EXE_NAME=%%F"
if not defined EXE_NAME (echo The server exe is missing next to uninstall.bat. & pause & exit /b 1)
set "NAME=%EXE_NAME:-mcp-server.exe=%"
set "SRV=%LOCALAPPDATA%\CsiMcp\%NAME%"
set "TOOL=%SRV%\%EXE_NAME%"
if not exist "%TOOL%" set "TOOL=%~dp0%EXE_NAME%"
"%TOOL%" --remove-clients
del /q "%SRV%\%EXE_NAME%" "%SRV%\claude_desktop_config_snippet.json" "%SRV%\configure_clients_result.txt" 2>nul
rmdir "%SRV%" 2>nul
echo.
echo Uninstalled the %NAME% server. Restart Claude Desktop.
echo.
echo This window closes in 10 seconds (press any key to close it now).
timeout /t 10 >nul
