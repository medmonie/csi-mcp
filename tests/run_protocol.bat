@echo off
rem Starts the Python server over stdio as Claude Desktop does and checks the protocol. Output: tests\results\protocol.txt
if not exist "%~dp0results" mkdir "%~dp0results"
"%~dp0..\server\.venv\Scripts\python" "%~dp0protocol_check.py" > "%~dp0results\protocol.txt" 2>&1
