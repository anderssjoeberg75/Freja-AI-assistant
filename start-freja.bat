@echo off
cd /d "%~dp0"

REM Where the backend lives. This machine runs only the client HUD; the backend runs on a
REM LAN server, so point the proxy at that server. Edit the address below if it changes, or
REM set BACKEND_URL in the environment before launching to override without editing this file.
REM (In "direct mode" - Backend API URL filled in inside the HUD Settings - the browser talks
REM to the backend straight and this value is unused, so it is safe to keep either way.)
if "%BACKEND_URL%"=="" set BACKEND_URL=http://192.168.107.15:8000

start "FREJA Client" /min "%CD%\venv\Scripts\python.exe" run_client.py

