# Registers a Task Scheduler task that starts TrackingGold.exe when you log on to Windows
# and restarts it if it exits. Runs in your user session (MT5 needs a desktop to toggle Algo Trading).
# Usage:  powershell -ExecutionPolicy Bypass -File install_autostart.ps1
#         powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -Remove
param([switch]$Remove)
$ErrorActionPreference = "Stop"
$name = "TrackingGold"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host "Removed task $name"
    return
}

$exe = Join-Path $PSScriptRoot "TrackingGold.exe"
if (-not (Test-Path $exe)) { throw "Not found: $exe" }

$action   = New-ScheduledTaskAction -Execute $exe -WorkingDirectory $PSScriptRoot
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = "PT30S"   # give the network a moment after logon
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force | Out-Null
Write-Host "Registered task $name -> $exe"
Write-Host "Start now with:  Start-ScheduledTask -TaskName $name"
