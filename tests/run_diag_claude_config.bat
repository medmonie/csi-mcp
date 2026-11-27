@echo off
rem Read-only: lists every Claude Desktop config file and its MCP servers. Output: tests\results\diag_claude_config.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0..\tools\diag_claude_config.py" > "%~dp0results\diag_claude_config.txt" 2>&1
