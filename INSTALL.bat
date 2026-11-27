@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Local SessionKeeper v2.49 - Installer
rem Switches: /quiet (no final pause)  /force (reinstall everything)  /nocuda (never download the CUDA runtime)
set "QUIET=" & set "FORCE=" & set "NOCUDA="
for %%A in (%*) do (
 if /I "%%~A"=="/quiet" set "QUIET=1"
 if /I "%%~A"=="/force" set "FORCE=1"
 if /I "%%~A"=="/nocuda" set "NOCUDA=1"
)
if not exist "logs" mkdir "logs"
set "LSK_LOG=%CD%\logs\LocalSessionKeeper.log"
echo.>>"%LSK_LOG%"
echo =====================================================>>"%LSK_LOG%"
echo [%date% %time%] Local SessionKeeper v2.49 installation started>>"%LSK_LOG%"
echo =====================================================>>"%LSK_LOG%"
echo Local SessionKeeper v2.49 - Complete installation
echo Log: %LSK_LOG%
where py >nul 2>nul
if errorlevel 1 (echo Python 3.11 x64 launcher not found. See README. & goto :failed)
if not exist ".venv\Scripts\python.exe" (
 echo Creating Python environment...
 py -3.11 -m venv .venv >>"%LSK_LOG%" 2>&1
 if errorlevel 1 goto :failed
)
set "PY=.venv\Scripts\python.exe"

echo [1/4] Main application dependencies...
"%PY%" -m app.install_state check main
if errorlevel 1 (set "NEED=1") else (set "NEED=")
if defined FORCE set "NEED=1"
if defined NEED (
 echo Installing...
 "%PY%" app\run_logged.py --log "%LSK_LOG%" -- "%PY%" -m pip install --disable-pip-version-check -r requirements.txt
 if errorlevel 1 goto :failed
) else (
 echo Already installed and verified - skipped.
)

echo [2/4] CUDA runtime for GPU transcription...
if defined NOCUDA (
 echo Skipped because /nocuda was given.
) else (
 "%PY%" -m app.cuda_check
 if not errorlevel 1 (
  echo Already installed - nothing to download.
 ) else (
  where nvidia-smi >nul 2>nul
  if errorlevel 1 (
   echo No NVIDIA GPU detected - skipping the CUDA download. CPU transcription will be used.
  ) else (
   echo Downloading the CUDA 12.8 and cuDNN 9 runtime, about 850 MB, one time only...
   "%PY%" app\run_logged.py --log "%LSK_LOG%" -- "%PY%" -m app.install_cuda_runtime
   if errorlevel 1 echo WARNING: CUDA runtime setup failed. CPU transcription remains available; see the log.
  )
 )
)

echo [3/4] Verifying main dependencies...
"%PY%" app\run_logged.py --log "%LSK_LOG%" -- "%PY%" -m app.install_check
if errorlevel 1 goto :failed
"%PY%" -m app.install_state save main

echo [4/4] Speaker-identification dependencies...
set "FORCEARG="
if defined FORCE set "FORCEARG=/force"
call INSTALL_DIARIZATION.bat /quiet %FORCEARG%
if errorlevel 1 goto :failed
echo [%date% %time%] COMPLETE INSTALLATION SUCCESS>>"%LSK_LOG%"
echo Installation complete. Transcription and speaker identification are installed.
if not defined QUIET pause
exit /b 0
:failed
echo [%date% %time%] INSTALLATION FAILED - see preceding log output>>"%LSK_LOG%"
echo Installation failed. See %LSK_LOG%
if not defined QUIET pause
exit /b 1
