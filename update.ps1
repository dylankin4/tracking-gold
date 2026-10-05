# Updates TrackingGold.exe on the VPS from the latest GitHub release.
# Never touches .env, the Telegram session or logs.
#
#   powershell -ExecutionPolicy Bypass -File update.ps1                  # update if a newer release exists
#   powershell -ExecutionPolicy Bypass -File update.ps1 -Force           # skip the "recent signal" safety check
#   powershell -ExecutionPolicy Bypass -File update.ps1 -Rollback        # go back to the previous exe
#   powershell -ExecutionPolicy Bypass -File update.ps1 -InstallSchedule # auto-update every Saturday 10:00
#
# For a private repo, put a read-only fine-grained token in the GITHUB_TOKEN user environment variable.
param(
    [string]$Repo = "dylankin4/tracking-gold",
    [switch]$Force,
    [switch]$Rollback,
    [switch]$InstallSchedule,
    [int]$QuietMinutes = 15
)
$ErrorActionPreference = "Stop"
$Dir = $PSScriptRoot
$TaskName = "TrackingGold"
$Exe = Join-Path $Dir "TrackingGold.exe"
$PrevExe = Join-Path $Dir "TrackingGold.prev.exe"
$VersionFile = Join-Path $Dir "version.txt"
$Log = Join-Path $Dir "logs\tracking.log"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Say($msg) { Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg" }

# Log lines matching $pattern written at or after $since (continuation lines without a timestamp are skipped)
function Get-LogLinesSince($pattern, $since, $tail) {
    if (-not (Test-Path $Log)) { return @() }
    Get-Content $Log -Tail $tail | Where-Object {
        # The timestamp match must come last: each -match overwrites $Matches
        $_ -match $pattern -and $_ -match "^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)" -and
        [datetime]::ParseExact($Matches[1], "yyyy-MM-dd HH:mm:ss", $null) -ge $since
    }
}

if ($InstallSchedule) {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-ExecutionPolicy Bypass -NoProfile -File `"$PSCommandPath`"" -WorkingDirectory $Dir
    # Saturday: the gold market is closed, so a restart can't miss a signal
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 10:00
    Register-ScheduledTask -TaskName "$TaskName-Update" -Action $action -Trigger $trigger -Force | Out-Null
    Say "Registered task '$TaskName-Update' (Saturdays 10:00)"
    return
}

function Stop-Bot {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Get-Process TrackingGold -ErrorAction SilentlyContinue | Stop-Process -Force
    for ($i = 0; $i -lt 20 -and (Get-Process TrackingGold -ErrorAction SilentlyContinue); $i++) { Start-Sleep 1 }
}

function Start-Bot {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { Start-ScheduledTask -TaskName $TaskName }
    else { Start-Process $Exe -WorkingDirectory $Dir }
}

# True if the bot started and logged "Listening to channel" after $since
function Test-BotHealthy($since) {
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep 2
        if (-not (Get-Process TrackingGold -ErrorAction SilentlyContinue)) { continue }
        if (Get-LogLinesSince "Listening to channel" $since 50) { return $true }
    }
    return $false
}

if ($Rollback) {
    if (-not (Test-Path $PrevExe)) { throw "No previous version to roll back to ($PrevExe missing)" }
    Stop-Bot
    Copy-Item $PrevExe $Exe -Force
    Remove-Item $VersionFile -ErrorAction SilentlyContinue
    Start-Bot
    Say "Rolled back to the previous exe"
    return
}

# --- Find the latest release ---
$headers = @{ "User-Agent" = "TrackingGold-updater"; "Accept" = "application/vnd.github+json" }
$token = [Environment]::GetEnvironmentVariable("GITHUB_TOKEN", "User")
if ($token) { $headers["Authorization"] = "Bearer $token" }
$release = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest" -Headers $headers
$latest = $release.tag_name.TrimStart("v")
$current = if (Test-Path $VersionFile) { (Get-Content $VersionFile -Raw).Trim() } else { "unknown" }
Say "Installed: $current   Latest: $latest"
if ($current -eq $latest) { Say "Already up to date"; return }

# --- Safety: don't restart right after a signal (orders may still be being placed/managed) ---
if (-not $Force) {
    $cutoff = (Get-Date).AddMinutes(-$QuietMinutes)
    if (Get-LogLinesSince "Signal received" $cutoff 500) { Say "A signal arrived in the last $QuietMinutes minutes - not updating now (use -Force to override)"; exit 1 }
}

# --- Download ---
$asset = $release.assets | Where-Object { $_.name -like "TrackingGold-*.zip" } | Select-Object -First 1
if (-not $asset) { throw "Release $($release.tag_name) has no TrackingGold-*.zip asset" }
$tmp = Join-Path $env:TEMP "TrackingGold-update"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $tmp | Out-Null
$zip = Join-Path $tmp $asset.name
$dlHeaders = $headers.Clone(); $dlHeaders["Accept"] = "application/octet-stream"
Invoke-WebRequest $asset.url -Headers $dlHeaders -OutFile $zip -UseBasicParsing
Expand-Archive $zip -DestinationPath $tmp -Force
Get-ChildItem $tmp -File | Unblock-File
if (-not (Test-Path (Join-Path $tmp "TrackingGold.exe"))) { throw "TrackingGold.exe missing from $($asset.name)" }
Say "Downloaded $($asset.name)"

# --- Swap the exe, keep the old one for rollback ---
Stop-Bot
if (Test-Path $Exe) { Copy-Item $Exe $PrevExe -Force }
Copy-Item (Join-Path $tmp "TrackingGold.exe") $Exe -Force
foreach ($f in ".env.example", "install_autostart.ps1", "setup_vps.ps1", "update.ps1") {
    $src = Join-Path $tmp $f
    if (Test-Path $src) { Copy-Item $src (Join-Path $Dir $f) -Force }
}

$since = Get-Date
Start-Bot
if (Test-BotHealthy $since) {
    Set-Content $VersionFile $latest
    Say "Updated to $latest - bot is running"
} else {
    Say "New version did not start correctly - rolling back"
    Stop-Bot
    if (Test-Path $PrevExe) { Copy-Item $PrevExe $Exe -Force }
    Start-Bot
    exit 1
}
