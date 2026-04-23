@echo off
title Bot Launcher
cd /d "C:\Users\0_o\Desktop\bot"

echo ===============================
echo   Start Bot...
echo ===============================
echo.

call venv\Scripts\activate.bat

echo [1/2] Started...
python main.py

echo.
echo ===============================
echo   Bot has been stopped.
echo ===============================
pause