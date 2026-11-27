@echo off
rem Pushes this folder to https://github.com/medmonie/csi-mcp in one commit (create the empty repository on GitHub
rem first). Commit message from tools\commit_message.txt. Log: publish_log.txt
rem Git's own output goes to the window, not to the log: a background git process (credential manager, maintenance)
rem can keep a redirected log file open, and the next write then fails.
setlocal
cd /d "%~dp0"
set "LOG=%~dp0publish_log.txt"
echo CSI MCP publish  %DATE% %TIME% > "%LOG%" 2>nul || set "LOG=%~dp0publish_log_%RANDOM%.txt"
echo CSI MCP publish  %DATE% %TIME% > "%LOG%"
where git >nul 2>nul || (echo GIT_MISSING >> "%LOG%" & echo Git is not installed: https://git-scm.com/download/win & pause & exit /b 1)
if not exist "tools\commit_message.txt" (echo Write the commit message into tools\commit_message.txt first. & pause & exit /b 1)
if not exist ".git" git init -b main
git remote get-url origin >nul 2>nul || git remote add origin https://github.com/medmonie/csi-mcp.git
git config core.autocrlf false
git config maintenance.auto false
git config gc.autoDetach false
git config user.name >nul 2>nul || git config user.name "Mohamed A. Hamed"
git config user.email >nul 2>nul || git config user.email "medmonie@users.noreply.github.com"
git fetch origin main 2>nul && git reset --mixed origin/main
echo [1/3] staging files
git add -A || (echo STEP=add >> "%LOG%" & goto failed)
git diff --cached --quiet && (echo NOTHING_TO_COMMIT >> "%LOG%" & echo Nothing changed since the last push. & echo RESULT=OK >> "%LOG%" & timeout /t 10 >nul & exit /b 0)
git diff --cached --name-status > "%TEMP%\csi_mcp_changes.txt" 2>nul
echo ---- changed files >> "%LOG%"
type "%TEMP%\csi_mcp_changes.txt" >> "%LOG%"
echo [2/3] committing
git commit -q -F "%~dp0tools\commit_message.txt" || (echo STEP=commit >> "%LOG%" & goto failed)
echo [3/3] pushing (sign in to GitHub if a window asks)
git push -u origin HEAD:main || (echo STEP=push >> "%LOG%" & goto failed)
git log -1 --oneline > "%TEMP%\csi_mcp_head.txt" 2>nul
type "%TEMP%\csi_mcp_head.txt" >> "%LOG%"
echo RESULT=OK >> "%LOG%"
echo Pushed to https://github.com/medmonie/csi-mcp
echo This window closes in 10 seconds.
timeout /t 10 >nul
exit /b 0
:failed
echo RESULT=FAILED >> "%LOG%"
echo Publishing failed. See %LOG%
pause
exit /b 1
