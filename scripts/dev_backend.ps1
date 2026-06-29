<#
.SYNOPSIS
  Reliable dev backend (re)start. Run this INSTEAD of `uvicorn --reload` directly.

.DESCRIPTION
  uvicorn's `--reload` on Windows can leave an ORPHAN worker bound to the port,
  serving STALE code after an edit (the reloader says "Reloading..." but the old
  worker keeps answering). A full kill-all + fresh start cannot serve stale code
  by construction, so this script is the trustworthy way to restart:

    1. Kill EVERY uvicorn process — reloader AND spawned worker (match `uvicorn`
       and `spawn_main`), so no orphan lingers.
    2. Wait for port 8000 to actually free.
    3. Start a fresh server.
    4. (-Verify) After it's up, hit a live endpoint to confirm it's answering.

  Just run it again any time you want a guaranteed-clean restart after a backend
  edit — it's idempotent.

.PARAMETER NoReload
  Start WITHOUT --reload (fully deterministic — re-run this script to pick up
  edits). Recommended when --reload has been flaky.

.PARAMETER Verify
  Start in the background and poll until port 8000 is listening, then report.
  (A full kill-all + fresh start guarantees current code, so a "is it up?" check
  is all that's needed — no stale-code probe required.)

.EXAMPLE
  ./scripts/dev_backend.ps1
  ./scripts/dev_backend.ps1 -NoReload -Verify
#>
param(
  [switch]$NoReload,
  [switch]$Verify
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 1. Kill every stale uvicorn process (reloader + worker).
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'uvicorn' -or $_.CommandLine -match 'spawn_main' }
if ($procs) {
  foreach ($p in $procs) {
    Write-Host "killing stale uvicorn pid $($p.ProcessId)" -ForegroundColor Yellow
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  }
} else {
  Write-Host "no existing uvicorn process" -ForegroundColor DarkGray
}

# 2. Wait for port 8000 to free (max ~5s).
for ($i = 0; $i -lt 20; $i++) {
  if (-not (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) { break }
  Start-Sleep -Milliseconds 250
}
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
  throw "Port 8000 still bound after kill - investigate before starting."
}

# 3. Ensure a TLS cert exists for the CURRENT LAN IP (handles network moves with
#    no edits here — see dev_certs.ps1). Capture the resolved IP it reports.
$certOut = & (Join-Path $PSScriptRoot 'dev_certs.ps1')
$certOut | Where-Object { $_ -notmatch '^DEV_IP=' } | ForEach-Object { Write-Host $_ }
$devIp = ($certOut | Where-Object { $_ -match '^DEV_IP=' } | Select-Object -Last 1) -replace '^DEV_IP=', ''
if (-not $devIp) { $devIp = '0.0.0.0' }

# 4. Start fresh.
$env:DEV_MODE = 'true'
$env:PYTHONPATH = '.'
$env:PYTHONIOENCODING = 'utf-8'

$uvArgs = @(
  '-m', 'uvicorn', 'api.index:app',
  '--host', '0.0.0.0', '--port', '8000',
  '--ssl-keyfile=certs/dev-key.pem',
  '--ssl-certfile=certs/dev.pem'
)
if (-not $NoReload) { $uvArgs += '--reload' }

Write-Host "starting backend on https://${devIp}:8000 (reload=$(-not $NoReload))" -ForegroundColor Green

if ($Verify) {
  # Start in the background, then poll until the port is listening. Works on
  # Windows PowerShell 5.1 (no -SkipCertificateCheck / PS7-only cmdlets).
  $py = Join-Path $root 'venv\Scripts\python.exe'
  $job = Start-Process -FilePath $py -ArgumentList $uvArgs -PassThru -NoNewWindow
  Write-Host "waiting for startup (pid $($job.Id))..." -ForegroundColor DarkGray
  $ready = $false
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { $ready = $true; break }
  }
  if ($ready) { Write-Host "VERIFIED: backend is listening on https://${devIp}:8000" -ForegroundColor Green }
  else { Write-Host "WARN: backend not listening within timeout - check for errors." -ForegroundColor Red }
  Write-Host "Backend running in background (pid $($job.Id)). Re-run this script for a guaranteed-clean restart."
} else {
  # Foreground — Ctrl-C stops it. This is the normal way to run it.
  & (Join-Path $root 'venv\Scripts\python.exe') @uvArgs
}
