$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$desktopRoot = $PSScriptRoot
$targetTriple = "x86_64-pc-windows-msvc"
$binaryDir = Join-Path $desktopRoot "src-tauri\binaries"
$backendDist = Join-Path $desktopRoot "backend-dist"
$ffmpeg = Get-Command ffmpeg -ErrorAction Stop
$ffmpegPath = $ffmpeg.Source
$shimPath = [System.IO.Path]::ChangeExtension($ffmpegPath, ".shim")
if (Test-Path $shimPath) {
    $shimDefinition = Get-Content $shimPath -Raw
    if ($shimDefinition -notmatch 'path\s*=\s*"([^"]+)"') {
        throw "Unable to resolve ffmpeg Scoop shim: $shimPath"
    }
    $ffmpegPath = $Matches[1]
}

Push-Location (Join-Path $projectRoot "frontend")
try {
    npm ci
    npm run build
} finally {
    Pop-Location
}

New-Item -ItemType Directory -Force -Path $binaryDir | Out-Null
uv run pyinstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name blsync-backend `
    --distpath $backendDist `
    --workpath (Join-Path $desktopRoot "pyinstaller-build") `
    --specpath $desktopRoot `
    --collect-all bilibili_api `
    --collect-all yutto `
    --hidden-import aiosqlite `
    --add-data "$(Join-Path $projectRoot 'static');static" `
    (Join-Path $projectRoot "src\blsync\main.py")

Copy-Item `
    (Join-Path $backendDist "blsync-backend.exe") `
    (Join-Path $binaryDir "blsync-backend-$targetTriple.exe") `
    -Force
Copy-Item `
    $ffmpegPath `
    (Join-Path $binaryDir "ffmpeg-$targetTriple.exe") `
    -Force

Push-Location $desktopRoot
try {
    npm ci
    npx tauri icon app-icon.svg
    npm run build
} finally {
    Pop-Location
}

$releaseDir = Join-Path $desktopRoot "src-tauri\target\release"
Copy-Item `
    (Join-Path $binaryDir "blsync-backend-$targetTriple.exe") `
    (Join-Path $releaseDir "blsync-backend.exe") `
    -Force
Copy-Item `
    (Join-Path $binaryDir "ffmpeg-$targetTriple.exe") `
    (Join-Path $releaseDir "ffmpeg.exe") `
    -Force
