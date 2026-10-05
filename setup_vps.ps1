# Chuẩn bị một VPS Windows mới để chạy TrackingGold.
# Cách dùng: chép thư mục TrackingGold vào VPS (vd. C:\TrackingGold), chuột phải file này -> Run with PowerShell.
#   Hoặc:  powershell -ExecutionPolicy Bypass -File C:\TrackingGold\setup_vps.ps1
#   Thêm -SkipMt5 nếu đã cài MT5 rồi.
param([switch]$SkipMt5)
$ErrorActionPreference = "Stop"
$Dir = $PSScriptRoot

# --- Tự chạy lại với quyền Administrator ---
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    $argList = "-ExecutionPolicy Bypass -NoExit -File `"$PSCommandPath`""
    if ($SkipMt5) { $argList += " -SkipMt5" }
    Start-Process powershell -Verb RunAs -ArgumentList $argList
    return
}

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    [!]  $msg" -ForegroundColor Yellow }
$todo = New-Object System.Collections.Generic.List[string]

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# --- 1. Kiểm tra hệ điều hành ---
Step "Kiểm tra hệ điều hành"
$os = Get-CimInstance Win32_OperatingSystem
Write-Host "    $($os.Caption) (build $($os.BuildNumber))"
if ([int]$os.BuildNumber -lt 14393) {
    Warn "Hệ điều hành quá cũ (cần Windows Server 2016 / Windows 10 trở lên). Bot sẽ không chạy được."
    Read-Host "Nhấn Enter để thoát"
    return
}
Ok "Hệ điều hành được hỗ trợ"

# --- 2. Kiểm tra file của bot ---
Step "Kiểm tra file của bot trong $Dir"
foreach ($f in "TrackingGold.exe", ".env", "install_autostart.ps1") {
    if (-not (Test-Path (Join-Path $Dir $f))) { throw "Thiếu file $f trong $Dir" }
}
Get-ChildItem $Dir -Recurse -File | Unblock-File
if (Test-Path (Join-Path $Dir "tracking_session.session")) { Ok "Có file đăng nhập Telegram" }
else { $todo.Add("Chưa có tracking_session.session: lần chạy đầu exe sẽ hỏi số điện thoại + mã OTP Telegram") }
$envText = Get-Content (Join-Path $Dir ".env") -Raw
foreach ($k in "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "SIGNAL_CHANNEL", "MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER") {
    if ($envText -notmatch "(?m)^$k=\S+") { $todo.Add("Điền $k trong $Dir\.env") }
}
Ok "Đã kiểm tra .env"

# --- 3. Đồng bộ giờ ---
Step "Đồng bộ giờ hệ thống"
try {
    Set-Service w32time -StartupType Automatic
    Start-Service w32time -ErrorAction SilentlyContinue
    w32tm /config /manualpeerlist:"time.windows.com,0x9 pool.ntp.org,0x9" /syncfromflags:manual /update | Out-Null
    w32tm /resync /force | Out-Null
    Ok "Giờ hiện tại: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ($((Get-TimeZone).Id))"
} catch { Warn "Không đồng bộ được giờ: $_" }
$todo.Add("Kiểm tra múi giờ VPS đúng chưa (Settings -> Time & Language) - giờ hiện tại: $(Get-Date -Format 'HH:mm')")

# --- 4. Nguồn điện: không bao giờ ngủ ---
Step "Tắt chế độ ngủ"
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /hibernate off 2>$null
Ok "Máy sẽ không tự ngủ"

# --- 5. Windows Update: không tự khởi động lại ---
Step "Cấu hình Windows Update (chỉ thông báo, không tự khởi động lại)"
$wu = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
New-Item -Path $wu -Force | Out-Null
Set-ItemProperty $wu -Name NoAutoUpdate -Value 0 -Type DWord
Set-ItemProperty $wu -Name AUOptions -Value 2 -Type DWord   # 2 = thông báo trước khi tải
Set-ItemProperty $wu -Name NoAutoRebootWithLoggedOnUsers -Value 1 -Type DWord
Ok "Windows Update sẽ không tự cài và khởi động lại - nhớ tự cập nhật vào cuối tuần"

# --- 6. Windows Defender ---
Step "Thêm thư mục bot vào danh sách loại trừ của Windows Defender"
try { Add-MpPreference -ExclusionPath $Dir; Ok "Đã loại trừ $Dir" }
catch { Warn "Không có Windows Defender hoặc không thêm được: $_" }

# --- 7. Giao diện nhẹ, tắt Server Manager khi đăng nhập ---
Step "Tối ưu giao diện"
$fx = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects"
New-Item -Path $fx -Force | Out-Null
Set-ItemProperty $fx -Name VisualFXSetting -Value 2 -Type DWord
New-Item -Path "HKCU:\Software\Microsoft\ServerManager" -Force | Out-Null
Set-ItemProperty "HKCU:\Software\Microsoft\ServerManager" -Name DoNotOpenServerManagerAtLogon -Value 1 -Type DWord
Get-ScheduledTask -TaskName ServerManager -ErrorAction SilentlyContinue | Disable-ScheduledTask | Out-Null
Ok "Đã tắt hiệu ứng và Server Manager tự mở"

# --- 8. Visual C++ Runtime ---
Step "Kiểm tra Microsoft Visual C++ Runtime"
$vc = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" -ErrorAction SilentlyContinue
if ($vc -and $vc.Installed -eq 1) { Ok "Đã có (v$($vc.Version))" }
else {
    $vcExe = Join-Path $env:TEMP "vc_redist.x64.exe"
    Invoke-WebRequest "https://aka.ms/vs/17/release/vc_redist.x64.exe" -OutFile $vcExe -UseBasicParsing
    Start-Process $vcExe -ArgumentList "/install /quiet /norestart" -Wait
    Ok "Đã cài Visual C++ Runtime"
}

# --- 9. MetaTrader 5 ---
Step "Kiểm tra MetaTrader 5"
$terminals = @(Get-ChildItem "C:\Program Files", "C:\Program Files (x86)" -Filter terminal64.exe -Recurse -Depth 2 -ErrorAction SilentlyContinue)
if ($terminals.Count -gt 0) {
    $terminals | ForEach-Object { Ok "Đã có: $($_.FullName)" }
    if ($terminals.Count -gt 1 -and $envText -notmatch "(?m)^MT5_PATH=\S+") {
        $todo.Add("Có nhiều bản MT5 - điền MT5_PATH=<đường dẫn terminal64.exe đúng> trong .env")
    }
} elseif (-not $SkipMt5) {
    $mt5Exe = Join-Path $env:TEMP "mt5setup.exe"
    Write-Host "    Đang tải bộ cài MT5..."
    Invoke-WebRequest "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" -OutFile $mt5Exe -UseBasicParsing
    Write-Host "    Mở bộ cài MT5 - bấm Next/Finish để cài, script đợi đến khi cài xong..."
    Start-Process $mt5Exe -Wait
    Ok "Đã chạy bộ cài MT5"
} else { Warn "Chưa thấy MT5 (đã bỏ qua vì -SkipMt5)" }
$todo.Add("Mở MT5 -> File -> Login to Trade Account: tìm server 'FivePercentOnline-Real', đăng nhập, xem giá XAUUSD chạy")
$todo.Add("MT5 -> Tools -> Options -> Expert Advisors: tích 'Allow algorithmic trading', bỏ tích các ô 'Disable ... when ...'; bấm nút Algo Trading thành màu xanh")

# --- 10. Tự khởi động bot khi đăng nhập ---
Step "Đăng ký bot tự chạy khi đăng nhập Windows"
& (Join-Path $Dir "install_autostart.ps1")
Ok "Đã đăng ký (task 'TrackingGold')"

$todo.Add("Chạy thử: nhấp đúp $Dir\TrackingGold.exe, đợi thấy 'Listening to channel', rồi đóng cửa sổ")
$todo.Add("Bật tự đăng nhập Windows bằng Sysinternals Autologon (https://learn.microsoft.com/sysinternals/downloads/autologon) - tự nhập mật khẩu Windows")
$todo.Add("Khởi động lại VPS để kiểm tra: sau khi tự đăng nhập ~30 giây, bot phải tự chạy (xem $Dir\logs\tracking.log)")
$todo.Add("Thoát RDP bằng nút X (Disconnect), KHÔNG bấm Sign out")

# --- Việc còn lại ---
Write-Host "`n================ VIỆC CÒN LẠI BẠN CẦN LÀM ================" -ForegroundColor Magenta
$i = 1; foreach ($t in $todo) { Write-Host " $i. $t"; $i++ }
Write-Host "==========================================================`n" -ForegroundColor Magenta
Read-Host "Nhấn Enter để đóng"
