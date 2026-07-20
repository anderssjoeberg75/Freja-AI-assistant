@echo off
cd /d "%~dp0"
start "FREJA Backend" /min "%CD%\venv\Scripts\python.exe" server.py
timeout /t 5 /nobreak >nul
start "FREJA Client" /min "%CD%\venv\Scripts\python.exe" run_client.py
