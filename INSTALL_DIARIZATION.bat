@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Local SessionKeeper v2.49 - Speaker identification setup
set "QUIET=" & set "FORCE="
for %%A in (%*) do (
 if /I "%%~A"=="/quiet" set "QUIET=1"
 if /I "%%~A"=="/force" set "FORCE=1"
)
if not exist "logs" mkdir "logs"
set "LSK_LOG=%CD%\logs\LocalSessionKeeper.log"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (echo Run INSTALL.bat first. & exit /b 1)
echo [%date% %time%] DIARIZATION DEPENDENCY CHECK STARTED>>"%LSK_LOG%"

"%PY%" -m app.install_state check diarization
if errorlevel 1 (set "NEED=1") else (set "NEED=")
if defined FORCE set "NEED=1"
if not defined NEED (
 echo Speaker-identification dependencies already installed and verified - skipped.
 goto :ready
)

rem One pip run for PyTorch and pyannote together, so nothing is installed and then replaced.
echo Installing PyTorch CUDA 12.8 and pyannote...
"%PY%" app\run_logged.py --log "%LSK_LOG%" -- "%PY%" -m pip install --disable-pip-version-check --extra-index-url https://download.pytorch.org/whl/cu128 -r requirements-torch.txt -r requirements-diarization.txt
if errorlevel 1 goto :failed
rem pyannote lists the optional TorchCodec decoder as a dependency; SessionKeeper decodes audio itself, so remove it once.
"%PY%" -m pip show torchcodec >nul 2>nul
if not errorlevel 1 (
 echo Removing the unused TorchCodec decoder...
 "%PY%" app\run_logged.py --log "%LSK_LOG%" -- "%PY%" -m pip uninstall -y torchcodec
 if errorlevel 1 goto :failed
)
echo Verifying speaker-identification dependencies...
"%PY%" app\run_logged.py --log "%LSK_LOG%" -- "%PY%" -m app.check_diarization_deps
if errorlevel 1 goto :failed
"%PY%" -m app.install_state save diarization
:ready
echo [%date% %time%] DIARIZATION DEPENDENCIES READY>>"%LSK_LOG%"
echo Speaker identification is ready. First use requires a Hugging Face read token and acceptance of:
echo https://huggingface.co/pyannote/speaker-diarization-community-1
if not defined QUIET pause
exit /b 0
:failed
echo [%date% %time%] DIARIZATION INSTALLATION FAILED>>"%LSK_LOG%"
echo Speaker-identification installation failed. See %LSK_LOG%
if not defined QUIET pause
exit /b 1
