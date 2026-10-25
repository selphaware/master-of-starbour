<#
.SYNOPSIS
  Launch Master of Starbour from source (KIT-08): starts the launcher, which owns
  the engine and the four terminals (TL, BUS-06).

.USAGE
  .\launch.ps1                 # normal play
  .\launch.ps1 -Seed 1234      # pin the world seed (overrides [world] seed)
  .\launch.ps1 -Config .\my.ini
  .\launch.ps1 -Heartbeat      # write terminal heartbeats to .\verify\artifacts\heartbeat (D-66)

Every path is explicit. Never bare `python` (Microsoft Store stub).
#>
[CmdletBinding()]
param(
  [int]$Seed,
  [string]$Config = "",
  [switch]$Heartbeat
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $Root "MOS\Scripts\python.exe"

if (-not (Test-Path $Py)) {
  Write-Error "Interpreter not found at $Py — create the venv MOS first (docs\SETUP.md step 1)."
}
if (-not (Test-Path (Join-Path $Root "starbour\__main__.py"))) {
  Write-Error "starbour package not built yet — run milestone M1 first (specs\M1.md)."
}

$env:PYTHONUTF8 = "1"
if ($Heartbeat) {
  $hb = Join-Path $Root "verify\artifacts\heartbeat"
  New-Item -ItemType Directory -Force -Path $hb | Out-Null
  $env:MOS_HEARTBEAT_DIR = $hb
}

$args = @("-m", "starbour")
if ($PSBoundParameters.ContainsKey("Seed")) { $args += @("--seed", "$Seed") }
if ($Config -ne "") { $args += @("--config", $Config) }

Set-Location $Root
& $Py @args
exit $LASTEXITCODE
