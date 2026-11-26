@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Local SessionKeeper - check for newer package versions
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (echo Run INSTALL.bat first. & pause & exit /b 1)
echo Installed versions:
"%PY%" -m pip list --disable-pip-version-check 2>nul | findstr /I /B /C:"faster-whisper" /C:"ctranslate2" /C:"pyannote" /C:"huggingface" /C:"torch" /C:"PySide6 " /C:"numpy" /C:"onnxruntime"
echo.
echo Newest versions published (first line of each is the latest):
for %%P in (faster-whisper ctranslate2 pyannote.audio huggingface_hub PySide6 numpy) do (
 echo --- %%P
 "%PY%" -m pip index versions %%P --disable-pip-version-check 2>nul | findstr /B /C:"%%P" /C:"  LATEST" /C:"  INSTALLED"
)
echo --- torch ^(CUDA 12.8 build^)
"%PY%" -m pip index versions torch --index-url https://download.pytorch.org/whl/cu128 --disable-pip-version-check 2>nul | findstr /B /C:"torch" /C:"  LATEST" /C:"  INSTALLED"
echo.
echo Nothing has been changed. Send me this output before upgrading anything: several packages are pinned on purpose.
pause
