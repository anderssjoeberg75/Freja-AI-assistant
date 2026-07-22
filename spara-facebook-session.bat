@echo off
cd /d "%~dp0"
echo ============================================================
echo  F.R.E.J.A. - Facebook Session Saver
echo ============================================================
echo  Detta skript opnar ett weblasarfonster pa din skarm.
echo  Logga in pa Facebook i fonstret for att spara din session.
echo ============================================================
echo.

venv\Scripts\python.exe save_session.py

echo.
pause
