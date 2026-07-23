@echo off
REM Open Antigravity IDE on the Freja repo (this script's folder).
REM Used by the "Claude leads, you click Run" workflow: Claude queues a task on
REM .agents/BOARD.md, this opens Antigravity so you just press Run on it.
setlocal
set "ANTIGRAVITY=%LOCALAPPDATA%\Programs\Antigravity IDE\bin\antigravity-ide.cmd"
if not exist "%ANTIGRAVITY%" (
  echo Antigravity CLI not found at:
  echo   %ANTIGRAVITY%
  echo Update the path in open-antigravity.bat if Antigravity is installed elsewhere.
  exit /b 1
)
call "%ANTIGRAVITY%" "%~dp0."
endlocal
