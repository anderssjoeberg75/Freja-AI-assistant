@echo off
REM Open Antigravity IDE on the Freja repo (this script's folder).
REM Part of the "Claude leads, you click Run" workflow: Claude queues a task on
REM .agents\BOARD.md, this opens Antigravity so you only press Run on it.
setlocal
set "ANTIGRAVITY=%LOCALAPPDATA%\Programs\Antigravity IDE\bin\antigravity-ide.cmd"
if not exist "%ANTIGRAVITY%" goto :missing
call "%ANTIGRAVITY%" "%~dp0."
if errorlevel 1 goto :failed
goto :eof

:missing
echo Antigravity CLI not found at:
echo   %ANTIGRAVITY%
echo Edit open-antigravity.bat if Antigravity is installed elsewhere.
pause
goto :eof

:failed
echo Antigravity failed to start (exit code %errorlevel%).
pause
