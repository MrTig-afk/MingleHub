<#
.SYNOPSIS
  Ensure a dev TLS cert exists for the CURRENT LAN IP. Run before the dev
  servers (dev_backend.ps1 calls it automatically; the Vite side just reads the
  files it writes).

.DESCRIPTION
  Phone testing reaches the laptop over HTTPS at https://<lan-ip>:<port>. A cert
  is only valid for the IPs baked into it, so every time the laptop's IP changes
  (new Wi-Fi, café, hotspot) a cert pinned to the OLD IP fails the name match and
  the phone rejects both the Vite app and the API.

  This script removes that per-network edit:
    1. Detect the current LAN IPv4 (the adapter that holds the default gateway).
    2. If certs/dev.pem already covers that IP (tracked in certs/.dev-ip), do
       nothing.
    3. Otherwise mint a fresh leaf cert with mkcert for `localhost 127.0.0.1
       <ip>`, written to the FIXED names certs/dev.pem + certs/dev-key.pem.

  Because the leaf is signed by the same mkcert root CA already trusted on the
  phone, the new cert is trusted with ZERO re-setup on the phone. The launchers
  point at the fixed filenames, so no edit to vite.config.js or dev_backend.ps1
  is ever needed again.

.OUTPUTS
  Writes the resolved IP to stdout's last line as "DEV_IP=<ip>" so callers can
  capture it.
#>
param(
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$certDir = Join-Path $root 'certs'
$certFile = Join-Path $certDir 'dev.pem'
$keyFile = Join-Path $certDir 'dev-key.pem'
$ipMarker = Join-Path $certDir '.dev-ip'

# 1. Resolve the current LAN IPv4 — the adapter that owns the default gateway is
#    the one a phone on the same network routes to.
$ip = $null
$cfg = Get-NetIPConfiguration -ErrorAction SilentlyContinue |
  Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } |
  Select-Object -First 1
if ($cfg) { $ip = $cfg.IPv4Address.IPAddress }

if (-not $ip) {
  # Fallback: first non-loopback, non-APIPA private IPv4.
  $ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } |
    Select-Object -First 1).IPAddress
}
if (-not $ip) { throw "Could not determine a LAN IPv4 address." }

# 2. Skip if the existing cert already covers this exact IP.
$haveIp = ''
if (Test-Path $ipMarker) { $haveIp = (Get-Content $ipMarker -Raw).Trim() }
if (-not $Force -and (Test-Path $certFile) -and (Test-Path $keyFile) -and $haveIp -eq $ip) {
  Write-Host "dev cert already valid for $ip - reusing certs/dev.pem" -ForegroundColor DarkGray
  Write-Output "DEV_IP=$ip"
  return
}

# 3. Mint a fresh leaf for the current IP under the FIXED filenames.
if (-not (Test-Path $certDir)) { New-Item -ItemType Directory -Path $certDir | Out-Null }

$mkcert = (Get-Command mkcert -ErrorAction SilentlyContinue).Source
if (-not $mkcert) { throw "mkcert not found on PATH. Install it (winget install FiloSottile.mkcert) and run 'mkcert -install' once." }

Write-Host "minting dev cert for $ip (localhost, 127.0.0.1, $ip)..." -ForegroundColor Green
& $mkcert -cert-file $certFile -key-file $keyFile localhost 127.0.0.1 $ip
if ($LASTEXITCODE -ne 0) { throw "mkcert failed (exit $LASTEXITCODE)." }

Set-Content -Path $ipMarker -Value $ip -Encoding ascii -NoNewline
Write-Host "wrote certs/dev.pem + certs/dev-key.pem for $ip" -ForegroundColor Green
Write-Output "DEV_IP=$ip"
