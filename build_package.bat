@echo off
rem Builds the distributables in dist\ (log: package_build.txt). One server build, packaged once per program:
rem   EtabsMcp.zip / Sap2000Mcp.zip / SafeMcp.zip   etabs- / sap2000- / safe-mcp-server.exe (no Python needed; the exe
rem                                                takes its program from its name), install.bat / uninstall.bat,
rem                                                INSTALL.txt, ACTIONS.md of that program
rem   CsiMcp-source.zip  clean source tree (no .venv, dist, results, logs)
rem Needs on THIS machine only: server\.venv (run setup_server.bat once).
setlocal
cd /d "%~dp0"
set "LOG=%~dp0package_build.txt"
echo CSI MCP package build  %DATE% %TIME% > "%LOG%"
if not exist "server\.venv\Scripts\python.exe" (echo server\.venv missing: run setup_server.bat first >> "%LOG%" & goto fail)
if exist dist rmdir /s /q dist
set "OUT=dist\_build"
mkdir "%OUT%"

echo [1/4] offline tests >> "%LOG%"
server\.venv\Scripts\python -m pytest tests -q -p no:cacheprovider >> "%LOG%" 2>&1 || goto fail

echo [2/4] server exe >> "%LOG%"
server\.venv\Scripts\python -m pip install -r server\requirements.txt pyinstaller >> "%LOG%" 2>&1 || goto fail
server\.venv\Scripts\python -m PyInstaller --onefile --noconfirm --name csi-mcp-server --collect-all mcp --paths server ^
  --hidden-import catalog --hidden-import edition --hidden-import actions --hidden-import csi_bridge --hidden-import table_tools ^
  --hidden-import client_config --hidden-import stdio_transport --hidden-import windows_lock --hidden-import safety --hidden-import model_read --hidden-import modelling --hidden-import definitions --hidden-import objects --hidden-import db_edit --hidden-import tables_edit --hidden-import geometry --hidden-import model_spec --hidden-import design --hidden-import program_specific --hidden-import autoload --hidden-import slabs --hidden-import sap_objects --hidden-import sap_analysis --hidden-import doc_export --hidden-import excel_export --hidden-import programs --hidden-import programs.sap2000 --hidden-import programs.etabs --hidden-import programs.safe --hidden-import results --hidden-import diagrams --hidden-import report --collect-all reportlab --collect-all openpyxl --collect-submodules comtypes ^
  --distpath "%OUT%" --workpath dist\_work --specpath dist\_work server\server.py >> "%LOG%" 2>&1 || goto fail
if not exist "%OUT%\csi-mcp-server.exe" goto fail

echo [3/4] program packages >> "%LOG%"
server\.venv\Scripts\python tools\gen_docs.py >> "%LOG%" 2>&1 || goto fail
server\.venv\Scripts\python tools\package_editions.py "%OUT%\csi-mcp-server.exe" >> "%LOG%" 2>&1 || goto fail

echo [4/4] source zip >> "%LOG%"
set "SRC=dist\_src\csi-mcp"
robocopy . "%SRC%" /E /NFL /NDL /NJH /NJS /XD dist .venv __pycache__ .git .pytest_cache results "CSI Solved Examples" "Claude outputs" ^
  /XF setup_result.txt package_build.txt publish_result.txt install_result.txt ^
  claude_config_snippet.json codex_config_snippet.toml commit_message.txt expected_v1.py *.zip >> "%LOG%" 2>&1
if errorlevel 8 goto fail
powershell -NoProfile -Command "Compress-Archive -Path dist\_src\csi-mcp -DestinationPath dist\CsiMcp-source.zip -Force" >> "%LOG%" 2>&1 || goto fail
rmdir /s /q dist\_src
rmdir /s /q dist\_work
rmdir /s /q dist\_build
echo RESULT=OK >> "%LOG%"
echo Packages built: dist\EtabsMcp.zip, dist\Sap2000Mcp.zip, dist\SafeMcp.zip  (see package_build.txt)
exit /b 0
:fail
echo RESULT=FAILED >> "%LOG%"
echo Package build failed: see package_build.txt
exit /b 1
