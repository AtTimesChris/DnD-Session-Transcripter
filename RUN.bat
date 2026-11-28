@echo off
setlocal
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
set "LSK_LOG=%CD%\logs\LocalSessionKeeper.log"
if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Run INSTALL.bat first.
    pause
    exit /b 1
)
echo.>>"%LSK_LOG%"
echo =====================================================>>"%LSK_LOG%"
echo [%date% %time%] APPLICATION STARTED>>"%LSK_LOG%"
echo =====================================================>>"%LSK_LOG%"
echo Local SessionKeeper v2.50 output is being saved to logs\LocalSessionKeeper.log
".venv\Scripts\python.exe" app\run_logged.py --log "%LSK_LOG%" -- ".venv\Scripts\python.exe" -m app.main
set "APP_EXIT=%ERRORLEVEL%"
echo [%date% %time%] APPLICATION EXIT CODE: %APP_EXIT%>>"%LSK_LOG%"
if not "%APP_EXIT%"=="0" (
    echo.
    echo Local SessionKeeper exited with an error. See logs\LocalSessionKeeper.log
    pause
)
exit /b %APP_EXIT%
