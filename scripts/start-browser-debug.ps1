<#
.SYNOPSIS
  Start Edge/Chrome with remote debugging for Desktop Agent attach (mode B).

.DESCRIPTION
  Mode B needs a CDP debugging port. By default this script relaunches the browser
  with your normal user profile so login state is reused.
  If the profile is locked by an already-running instance, close the browser first,
  or pass -IsolatedProfile to use a dedicated profile.

.EXAMPLE
  powershell -File scripts/start-browser-debug.ps1 -Browser edge
  powershell -File scripts/start-browser-debug.ps1 -Browser chrome -Port 9222
  powershell -File scripts/start-browser-debug.ps1 -Browser edge -IsolatedProfile
#>
param(
    [ValidateSet("edge", "chrome")]
    [string]$Browser = "edge",
    [int]$Port = 9222,
    [switch]$IsolatedProfile
)

$ErrorActionPreference = "Stop"

function Find-BrowserPath([string]$name) {
    if ($name -eq "edge") {
        $candidates = @(
            "$env:ProgramFiles (x86)\Microsoft\Edge\Application\msedge.exe",
            "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
        )
    } else {
        $candidates = @(
            "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
            "$env:ProgramFiles (x86)\Google\Chrome\Application\chrome.exe",
            "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
        )
    }
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Get-DefaultProfileDir([string]$name) {
    if ($name -eq "edge") {
        return Join-Path $env:LOCALAPPDATA "Microsoft\Edge\User Data"
    }
    return Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
}

$exe = Find-BrowserPath $Browser
if (-not $exe) {
    Write-Error "Cannot find $Browser executable."
    exit 1
}

$procName = if ($Browser -eq "edge") { "msedge" } else { "chrome" }
$running = Get-Process -Name $procName -ErrorAction SilentlyContinue
if ($running -and -not $IsolatedProfile) {
    Write-Warning "$Browser is already running. Windows often locks the default profile."
    Write-Warning "Close all $Browser windows, then re-run this script."
    Write-Warning "Or use -IsolatedProfile (separate profile; sign-in once)."
    $answer = Read-Host "Type YES to close all $procName processes and continue"
    if ($answer -ne "YES") {
        Write-Host "Aborted."
        exit 2
    }
    Stop-Process -Name $procName -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

if ($IsolatedProfile) {
    $profileDir = Join-Path $env:LOCALAPPDATA "DesktopAgent\browser-debug-profile\$Browser"
    New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
} else {
    $profileDir = Get-DefaultProfileDir $Browser
}

Write-Host "Starting $Browser with CDP on 127.0.0.1:$Port"
Write-Host "Executable : $exe"
Write-Host "User data  : $profileDir"
Write-Host "Next       : desktop-agent doctor   /   desktop-agent browser-probe"

# Chrome 136+ requires a non-default --user-data-dir for CDP.
# Do not pass --remote-debugging-address; localhost binding is the default.
$launchArgs = @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$profileDir",
    "--no-first-run",
    "--no-default-browser-check",
    "--new-window",
    "about:blank"
)

Start-Process -FilePath $exe -ArgumentList $launchArgs | Out-Null
Write-Host "Launched. Waiting for CDP..."

$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/json/version" -UseBasicParsing -TimeoutSec 1
        if ($resp.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 1
    }
}

if ($ready) {
    Write-Host "CDP is ready: http://127.0.0.1:$Port"
} else {
    Write-Error "Browser started but CDP is not reachable on port $Port. Close all $Browser windows and retry."
    exit 2
}
