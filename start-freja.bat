@echo off
cd /d "%~dp0"
set BACKEND_URL=http://192.168.107.15:8000
start "FREJA Client" /min "%CD%\venv\Scripts\python.exe" run_client.py
