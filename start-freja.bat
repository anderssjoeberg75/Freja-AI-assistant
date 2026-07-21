@echo off
cd /d "%~dp0"
if "%BACKEND_URL%"=="" set BACKEND_URL=http://localhost:8000
start "FREJA Client" /min "%CD%\venv\Scripts\python.exe" run_client.py

