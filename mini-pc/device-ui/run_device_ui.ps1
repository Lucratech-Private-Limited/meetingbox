# Run MeetingBox device UI from a local venv (Windows — no Docker rebuild).
#
# One-time:
#   cd mini-pc\device-ui
#   py -3 -m venv .venv
#   .\.venv\Scripts\Activate.ps1
#   pip install -r requirements.txt
#
# Usage:
#   .\run_device_ui.ps1
#   $env:MOCK_BACKEND = "1"; .\run_device_ui.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$MiniPcRoot = Split-Path -Parent $ScriptDir
$MonorepoRoot = $null
if (Test-Path (Join-Path $MiniPcRoot "..\server\docker-compose.yml")) {
    $MonorepoRoot = (Resolve-Path (Join-Path $MiniPcRoot "..")).Path
}

$VenvDir = if ($env:VENV_DIR) { $env:VENV_DIR } else { ".venv" }
$Py = Join-Path $ScriptDir "$VenvDir\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Error "Missing venv at $ScriptDir\$VenvDir — create with: py -3 -m venv .venv && .\.venv\Scripts\Activate.ps1 && pip install -r requirements.txt"
}

function Load-EnvFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.TrimEnd("`r")
        if ($line -match '^\s*#' -or $line -match '^\s*$') { return }
        $eq = $line.IndexOf('=')
        if ($eq -lt 1) { return }
        $name = $line.Substring(0, $eq).Trim()
        $val = $line.Substring($eq + 1).Trim()
        if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
            $val = $val.Substring(1, $val.Length - 2)
        }
        Set-Item -Path "Env:$name" -Value $val
    }
}

if ($MonorepoRoot) { Load-EnvFile (Join-Path $MonorepoRoot ".env") }
Load-EnvFile (Join-Path $MiniPcRoot ".env")
Load-EnvFile (Join-Path $ScriptDir ".env")

if (-not $env:BACKEND_URL -and $env:APP_BASE_URL) {
    $base = $env:APP_BASE_URL.TrimEnd('/')
    Set-Item -Path "Env:BACKEND_URL" -Value $base
}

if (-not $env:DISPLAY_WIDTH) { Set-Item -Path "Env:DISPLAY_WIDTH" -Value "1024" }
if (-not $env:DISPLAY_HEIGHT) { Set-Item -Path "Env:DISPLAY_HEIGHT" -Value "600" }

if (-not $env:BACKEND_URL -and $env:MOCK_BACKEND -ne "1") {
    Write-Warning "BACKEND_URL is not set — UI will use http://localhost:8000. Set BACKEND_URL in mini-pc\.env or use MOCK_BACKEND=1."
}

& $Py (Join-Path $ScriptDir "src\main.py") @args
