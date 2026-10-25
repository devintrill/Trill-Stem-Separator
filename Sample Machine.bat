@echo off
title Sample Machine
set "MSST=C:\Music-Source-Separation-Training"
set "PY=%MSST%\venv\Scripts\python.exe"

if not exist "%PY%" (
  echo Can't find MSST at %MSST%
  echo It needs to be installed there with its venv.
  pause
  exit /b
)

"%PY%" -c "import webview, clr, sounddevice, soundfile, yt_dlp, imageio_ffmpeg, librosa" >nul 2>&1
if errorlevel 1 (
  echo First run: installing the app's packages. This only happens once...
  "%PY%" -m pip install --disable-pip-version-check -q pywebview pythonnet sounddevice soundfile yt-dlp imageio-ffmpeg librosa
  if errorlevel 1 (
    echo Install failed. Check your internet connection and try again.
    pause
    exit /b
  )
)

"%PY%" -c "import deeprhythm, skey, nnAudio, einops" >nul 2>&1
if errorlevel 1 (
  echo Installing the BPM/key models. This only happens once...
  "%PY%" -m pip install --disable-pip-version-check -q --no-deps deeprhythm==0.0.13 nnAudio==0.3.3 einops "git+https://github.com/deezer/skey@918b83d"
)

"%PY%" -c "import madmom" >nul 2>&1
if errorlevel 1 (
  echo Installing the key model. This only happens once...
  "%PY%" -m pip install --disable-pip-version-check -q cython mido
  "%PY%" -m pip install --disable-pip-version-check -q --no-deps --no-build-isolation "git+https://github.com/CPJKU/madmom@27f032e8947204902c675e5e341a3faf5dc86dae"
)

rem Key detection uses Essentia through Node.js (essjs folder). Without Node the app uses the other key models.
where node >nul 2>&1
if errorlevel 1 (
  echo Installing Node.js for key detection. This only happens once...
  winget install -e --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
)
if not exist "%~dp0essjs\node_modules\essentia.js" (
  where npm >nul 2>&1
  if not errorlevel 1 (
    pushd "%~dp0essjs"
    call npm install --silent --no-audit --no-fund essentia.js@0.1.3 >nul 2>&1
    popd
  )
)

start "" "%MSST%\venv\Scripts\pythonw.exe" "%~dp0stem_splitter.py"
