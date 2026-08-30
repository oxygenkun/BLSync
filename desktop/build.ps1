$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$projectRoot = Split-Path -Parent $PSScriptRoot
$desktopRoot = $PSScriptRoot
$targetTriple = "x86_64-pc-windows-msvc"
$binaryDir = Join-Path $desktopRoot "src-tauri\binaries"
$backendDist = Join-Path $desktopRoot "backend-dist"

function Assert-WindowsExecutable([string]$Path, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Name executable does not exist: $Path"
    }
    $file = Get-Item -LiteralPath $Path
    if ($file.Length -lt 1MB) {
        throw "$Name executable is unexpectedly small ($($file.Length) bytes): $Path"
    }
    $stream = $file.OpenRead()
    try {
        if ($stream.ReadByte() -ne 0x4D -or $stream.ReadByte() -ne 0x5A) {
            throw "$Name is not a Windows PE executable: $Path"
        }
    } finally {
        $stream.Dispose()
    }
}

if ($env:BLSYNC_FFMPEG_PATH) {
    $ffmpegPath = (Resolve-Path -LiteralPath $env:BLSYNC_FFMPEG_PATH -ErrorAction Stop).Path
} else {
    $ffmpegPath = (Get-Command ffmpeg -CommandType Application -ErrorAction Stop).Source
    $shimPaths = @(
        [System.IO.Path]::ChangeExtension($ffmpegPath, ".shim"),
        "$ffmpegPath.shim"
    )
    foreach ($shimPath in $shimPaths) {
        if (Test-Path -LiteralPath $shimPath) {
            $shimDefinition = Get-Content -LiteralPath $shimPath -Raw
            if ($shimDefinition -match 'path\s*=\s*"([^"]+)"') {
                $ffmpegPath = (Resolve-Path -LiteralPath $Matches[1] -ErrorAction Stop).Path
                break
            }
        }
    }
}
Assert-WindowsExecutable $ffmpegPath "FFmpeg"
& $ffmpegPath -version
$ffmpegExitCode = $LASTEXITCODE
if ($ffmpegExitCode -ne 0) {
    throw "FFmpeg validation failed with exit code $ffmpegExitCode"
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

$backendExecutable = Join-Path $backendDist "blsync-backend.exe"
Assert-WindowsExecutable $backendExecutable "BLSync backend"

Copy-Item `
    $backendExecutable `
    (Join-Path $binaryDir "blsync-backend-$targetTriple.exe") `
    -Force
Copy-Item `
    $ffmpegPath `
    (Join-Path $binaryDir "ffmpeg-$targetTriple.exe") `
    -Force
Assert-WindowsExecutable (Join-Path $binaryDir "blsync-backend-$targetTriple.exe") "Bundled BLSync backend"
Assert-WindowsExecutable (Join-Path $binaryDir "ffmpeg-$targetTriple.exe") "Bundled FFmpeg"

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
