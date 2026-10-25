# SampleSmith setup. SampleSmith.exe runs this when something is missing; it's safe to run again.
# Everything goes in runtime\ next to the app (Python 3.11, PyTorch, MSST, Node), so deleting the
# folder removes it all. Settings and the log are in %APPDATA%\SampleSmith.

# Not 'Stop': in Windows PowerShell that turns any stderr output from uv, tar or nvidia-smi into a crash.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root    = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root 'runtime'
$Temp    = Join-Path $Runtime 'tmp'
$Venv    = Join-Path $Runtime 'venv'
$Py      = Join-Path $Venv 'Scripts\python.exe'
$Uv      = Join-Path $Runtime 'uv\uv.exe'
$Data    = Join-Path $env:APPDATA 'SampleSmith'

$UvUrl    = 'https://github.com/astral-sh/uv/releases/download/0.11.32/uv-x86_64-pc-windows-msvc.zip'
$MsstRev  = '6fdad13686bb7e84d029ec66f9dbe76e65c93ebf'
$MsstUrl  = "https://github.com/ZFTurbo/Music-Source-Separation-Training/archive/$MsstRev.zip"
$SkeyUrl  = 'https://github.com/deezer/skey/archive/918b83d273568d5041569bb8068843d19a335726.zip'
$NodeVer  = 'v24.22.1'
$NodeUrl  = "https://nodejs.org/dist/$NodeVer/node-$NodeVer-win-x64.zip"
$WebView2 = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'
$VcRedist = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'

$env:UV_PYTHON_INSTALL_DIR = Join-Path $Runtime 'python'
$env:UV_CACHE_DIR = Join-Path $Runtime 'uv-cache'
$env:UV_PYTHON_PREFERENCE = 'only-managed'
$env:UV_NO_PROGRESS = '1'
# No double quotes in Python code passed to python -c: Windows PowerShell 5.1 mangles them.
$Check = 'import sys, importlib.util as u; sys.exit(any(u.find_spec(m) is None for m in ''webview clr sounddevice soundfile yt_dlp imageio_ffmpeg librosa torch demucs omegaconf ml_collections deeprhythm skey nnAudio einops''.split()))'

New-Item -ItemType Directory -Force -Path $Runtime, $Temp, $Data | Out-Null
Start-Transcript -Path (Join-Path $Data 'install.log') -Force | Out-Null
$Host.UI.RawUI.WindowTitle = 'SampleSmith setup'

function Say($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }
function Step($n, $text) { Write-Host ''; Write-Host "[$n/9] $text" -ForegroundColor Cyan }

function Fail($text) {
    Write-Host ''
    Write-Host $text -ForegroundColor Red
    Write-Host 'Check your internet connection, then open SampleSmith.exe again to pick up where this stopped.' -ForegroundColor Red
    Write-Host "Details are in $Data\install.log"
    Stop-Transcript | Out-Null
    if (-not $env:SAMPLESMITH_NOPAUSE) { Read-Host 'Press Enter to close' }
    exit 1
}

function Download($url, $out) {
    for ($i = 1; $i -le 3; $i++) {
        try {
            if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
                & curl.exe -L --fail --silent --show-error --retry 2 -o $out $url
                if ($LASTEXITCODE -eq 0) { return }
            } else {
                Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $out -ErrorAction Stop
                return
            }
        } catch { }
        Start-Sleep -Seconds (2 * $i)
    }
    Fail "Couldn't download $url"
}

function Unzip($zip, $dest) {
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    & tar.exe -xf $zip -C $dest 2>$null
    if ($LASTEXITCODE -ne 0) { Expand-Archive -LiteralPath $zip -DestinationPath $dest -Force }
}

function Run($what) {
    # Runs a native command (a scriptblock) and stops setup if it fails.
    & $what
    if ($LASTEXITCODE -ne 0) { Fail "Setup failed while $($what.ToString().Trim())" }
}

function PyOk($code) {
    if (-not (Test-Path $Py)) { return $false }
    & $Py -c $code 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

Say '======================================' White
Say '  SampleSmith setup' White
Say '======================================' White
Say 'This downloads about 4 GB the first time, so it can take a while.'

# 1. Graphics card: pick the PyTorch build that supports it.
Step 1 'Checking your graphics card'
$TorchIndex = 'https://download.pytorch.org/whl/cu126'
$smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
if (-not $smi -and (Test-Path "$env:SystemRoot\System32\nvidia-smi.exe")) { $smi = "$env:SystemRoot\System32\nvidia-smi.exe" }
if ($smi) {
    $gpu = (& $smi --query-gpu=name,compute_cap --format=csv,noheader 2>$null | Select-Object -First 1)
    Say "  $gpu"
    $cap = 0.0
    if ($gpu -match ',\s*([\d.]+)') { $cap = [double]::Parse($Matches[1], [Globalization.CultureInfo]::InvariantCulture) }
    if ($cap -ge 12) { $TorchIndex = 'https://download.pytorch.org/whl/cu128' }   # RTX 50 series
} else {
    Say '  No NVIDIA graphics card found. SampleSmith needs one; without it splitting is very slow.' Yellow
}

# 2. Windows parts the app needs: WebView2 (the window) and the Visual C++ runtime (PyTorch).
Step 2 'Checking Windows components'
$wv = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
$hasWv = (Test-Path "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}") -or
         (Test-Path "HKLM:\$wv") -or (Test-Path "HKCU:\$wv")
if (-not $hasWv) {
    Say '  Installing Microsoft Edge WebView2...'
    Download $WebView2 "$Temp\webview2.exe"
    Start-Process "$Temp\webview2.exe" -ArgumentList '/silent', '/install' -Wait
}
$vc = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' -ErrorAction SilentlyContinue
if (-not $vc -or $vc.Installed -ne 1) {
    Say '  Installing the Microsoft Visual C++ runtime (Windows may ask for permission)...'
    Download $VcRedist "$Temp\vc_redist.x64.exe"
    try { Start-Process "$Temp\vc_redist.x64.exe" -ArgumentList '/install', '/quiet', '/norestart' -Verb RunAs -Wait }
    catch { Say '  Skipped. If the app later fails to start, install it from aka.ms/vs/17/release/vc_redist.x64.exe' Yellow }
}
Say '  OK'

# 3. uv: installs Python and the packages, much faster than pip.
Step 3 'Getting the installer tools'
if (-not (Test-Path $Uv)) {
    Download $UvUrl "$Temp\uv.zip"
    Unzip "$Temp\uv.zip" (Join-Path $Runtime 'uv')
    if (-not (Test-Path $Uv)) { Fail "uv didn't unpack." }
}
Say '  OK'

# 4. Python 3.11 in its own environment.
Step 4 'Setting up Python 3.11'
if (-not (PyOk 'import sys; assert sys.version_info[:2] == (3, 11)')) {
    if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }
    Run { & $Uv python install 3.11 }
    Run { & $Uv venv --python 3.11 $Venv }
}
Say '  OK'

# 5. PyTorch with CUDA (the big download).
Step 5 'Installing PyTorch (about 2.5 GB)'
if (-not (PyOk 'import torch, sys; sys.exit(0 if torch.version.cuda else 1)')) {
    Run { & $Uv pip install --python $Py torch torchaudio --index-url $TorchIndex }
}
Say '  OK'

# 6. Everything else.
Step 6 'Installing the app packages'
Run { & $Uv pip install --python $Py -r (Join-Path $PSScriptRoot 'requirements.txt') }
Run { & $Uv pip install --python $Py --no-deps demucs==4.0.0 "skey @ $SkeyUrl" }
Say '  OK'

# 7. MSST, the separation engine. Models it needs download the first time each one is used.
Step 7 'Getting the separation engine'
$Engine = Join-Path $Runtime 'engine'
if (-not (Test-Path (Join-Path $Engine 'inference.py'))) {
    Download $MsstUrl "$Temp\msst.zip"
    Unzip "$Temp\msst.zip" "$Temp\msst"
    $src = Join-Path "$Temp\msst" "Music-Source-Separation-Training-$MsstRev"
    if (-not (Test-Path (Join-Path $src 'inference.py'))) { Fail "The engine download didn't unpack." }
    if (Test-Path $Engine) { Remove-Item -Recurse -Force $Engine }
    Move-Item $src $Engine
}
# Reuse models from an older install (Trill Stem Separator kept MSST in C:\Music-Source-Separation-Training).
$Models = Join-Path $Runtime 'models'
$OldModels = 'C:\Music-Source-Separation-Training\models'
if (-not (Test-Path $Models) -and (Test-Path $OldModels)) {
    Say '  Copying the models you already have...'
    robocopy $OldModels $Models /E /NFL /NDL /NJH /NJS /NP | Out-Null
}
Say '  OK'

# 8. Node.js runs Essentia's key detection (and helps YouTube downloads).
Step 8 'Setting up key detection'
$NodeDir = Join-Path $Runtime 'node'
if (-not (Test-Path (Join-Path $NodeDir 'node.exe'))) {
    Download $NodeUrl "$Temp\node.zip"
    Unzip "$Temp\node.zip" "$Temp\node"
    if (Test-Path $NodeDir) { Remove-Item -Recurse -Force $NodeDir }
    Move-Item (Join-Path "$Temp\node" "node-$NodeVer-win-x64") $NodeDir
}
$Ess = Join-Path $Root 'app\essjs'
if (-not (Test-Path (Join-Path $Ess 'node_modules\essentia.js'))) {
    $env:Path = "$NodeDir;$env:Path"
    Push-Location $Ess
    & (Join-Path $NodeDir 'npm.cmd') install --no-audit --no-fund --silent
    Pop-Location
    if (-not (Test-Path (Join-Path $Ess 'node_modules\essentia.js'))) {
        Say '  Essentia did not install; key detection will use the other models.' Yellow
    }
}
Say '  OK'

# 9. Shortcuts, cleanup, start.
Step 9 'Finishing up'
$exe = Join-Path $Root 'SampleSmith.exe'
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'))) {
    $lnk = Join-Path $dir 'SampleSmith.lnk'
    if ((Test-Path $exe) -and -not (Test-Path $lnk)) {
        $s = $shell.CreateShortcut($lnk)
        $s.TargetPath = $exe
        $s.WorkingDirectory = $Root
        $s.Save()
    }
}
if (-not (PyOk $Check) -or -not (Test-Path (Join-Path $Engine 'inference.py'))) { Fail 'Some parts are still missing after setup.' }
Remove-Item -Recurse -Force $Temp -ErrorAction SilentlyContinue
& $Uv cache clean 2>$null | Out-Null

Say ''
Say 'SampleSmith is ready.' Green
Stop-Transcript | Out-Null
if (Test-Path $exe) { Start-Process $exe -WorkingDirectory $Root }
Start-Sleep -Seconds 2
