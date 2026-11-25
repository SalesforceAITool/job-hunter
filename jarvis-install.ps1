# Jarvis (Mika fork) install over USB - Samsung Galaxy Z Fold 7
# Follows jarvis/docs/install-with-agent.en.md (branch claude/personal-mobile-ai-assistant-u5xuvs).
# Rules: only installs/sets up Jarvis, never uninstalls anything, never touches your API key,
# and stops whenever a tap on the phone is needed.
# Run:  powershell -ExecutionPolicy Bypass -File .\jarvis-install.ps1

$ErrorActionPreference = 'Stop'
$Pkg    = 'com.wangzan101.mika'
$ApkUrl = 'https://github.com/SalesforceAITool/job-hunter/releases/download/jarvis-latest/Jarvis.apk'
$ApkSha = 'B29737D41977112908366ED495FC7C4D69AF14808F850F9050179D27CEF2BB1F'
$Apk    = Join-Path $env:USERPROFILE 'Downloads\Jarvis.apk'

function Say($m)   { Write-Host "`n>> $m" -ForegroundColor Cyan }
function Wait($m)  { Write-Host "`n[PHONE] $m" -ForegroundColor Yellow; Read-Host 'Ho gaya? Enter dabao' | Out-Null }
function Adb       { $out = & adb @args 2>&1 | Out-String; return $out.Trim() }

# --- 1.1 adb ---
Say 'adb check kar raha hoon'
if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
    Say 'adb nahi mila, winget se install kar raha hoon'
    winget install --id Google.PlatformTools -e --accept-source-agreements --accept-package-agreements
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
    if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
        $link = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet" -Recurse -Filter adb.exe -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($link) { $env:Path += ';' + $link.DirectoryName } else { throw 'adb install nahi hua. Terminal band karke dobara chalao.' }
    }
}
Adb version | Select-Object -First 1 | Write-Host

# --- 1.2 device ---
while ($true) {
    $lines = (Adb devices) -split "`n" | Where-Object { $_ -match "`t" }
    if ($lines.Count -eq 1 -and $lines[0] -match "`tdevice") { break }
    if ($lines -match 'unauthorized') { Wait 'Phone pe "Allow USB debugging?" aaya hoga -> "Always allow" tick karke Allow dabao.'; continue }
    if ($lines.Count -gt 1) { throw 'Ek se zyada device lage hain. Sirf Fold 7 lagao aur dobara chalao.' }
    Wait @'
Phone nahi dikh raha. 3 steps:
 1) Settings -> About phone -> Software information -> "Build number" 7 baar tap karo.
 2) Settings -> Developer options -> "USB debugging" ON.
 3) Cable lagao, notification se USB mode "File transfer" chuno, aur "Allow" dabao.
(Agar phir bhi na chale: Settings -> Security and privacy -> Auto Blocker -> Off)
'@
}
Say 'Phone connected.'

# --- 1.3 / 1.4 ---
$sdk = [int](Adb shell getprop ro.build.version.sdk)
$abi = Adb shell getprop ro.product.cpu.abi
Write-Host "Android SDK $sdk, CPU $abi"
if ($sdk -le 28) { throw 'Android bahut purana hai (Android 10+ chahiye).' }

# --- 2. download ---
if (-not (Test-Path $Apk) -or (Get-FileHash $Apk -Algorithm SHA256).Hash -ne $ApkSha) {
    Say 'Jarvis.apk download ho raha hai (~154 MB)...'
    try { Invoke-WebRequest -Uri $ApkUrl -OutFile $Apk -UseBasicParsing }
    catch {
        Wait "Download nahi hua (repo private ho sakta hai). Browser mein GitHub login karke ye link kholo aur file Downloads mein save karo:`n$ApkUrl"
    }
}
if ((Get-FileHash $Apk -Algorithm SHA256).Hash -ne $ApkSha) { Write-Host 'Warning: file ka checksum release se match nahi karta (shayad naya build aa gaya).' -ForegroundColor Red }

# --- 3. install ---
Say 'Install kar raha hoon. Phone pe koi "Install/Allow" aaye to tap karna.'
$r = Adb install -r $Apk
Write-Host $r
if ($r -notmatch 'Success') {
    if ($r -match 'VERSION_DOWNGRADE') { Write-Host 'Phone pe naya version pehle se hai, aage badhte hain.' }
    elseif ($r -match 'UPDATE_INCOMPATIBLE') { throw 'Purana Jarvis/Mika alag key se signed hai. Use hatana padega (chats/settings jayenge) - ye aapka faisla hai. Khud uninstall karke script dobara chalao.' }
    elseif ($r -match 'USER_RESTRICTED|ABORTED') { throw 'Install block hua. Settings -> Security and privacy -> Auto Blocker -> Off karo, phir dobara chalao.' }
    else { throw "Install fail: $r" }
}

# --- 4. notifications ---
if ($sdk -ge 33) { Say 'Notification permission'; Adb shell pm grant $Pkg android.permission.POST_NOTIFICATIONS | Write-Host }

# --- 5. accessibility (keeps existing services) ---
if ($sdk -ge 31) {
    Say 'Accessibility service ON (baaki services waise hi rahengi)'
    $me  = "$Pkg/.a11y.MikaAccessibilityService"
    $long = "$Pkg/$Pkg.a11y.MikaAccessibilityService"
    $cur = Adb shell settings get secure enabled_accessibility_services
    Write-Host "current: $cur"
    $list = if ($cur -eq 'null' -or -not $cur) { @() } else { $cur -split ':' }
    if ($list -notcontains $me -and $list -notcontains $long) { $list += $me }
    $new = $list -join ':'
    Adb shell settings put secure enabled_accessibility_services "'$new'" | Out-Null
    Adb shell settings put secure accessibility_enabled 1 | Out-Null
    Adb shell settings get secure enabled_accessibility_services | Write-Host
}

# --- 6. battery allowlist ---
Say 'Battery allowlist'
Adb shell cmd deviceidle whitelist "+$Pkg" | Write-Host

# --- 7. open app + start helper over USB (option B) ---
Say 'App khol raha hoon'
Adb shell am start -n "$Pkg/.ui.MainActivity" | Write-Host
Wait 'Phone pe Jarvis khul gaya. Model wala screen aaye to abhi "Not now" dabao. Kuch second ruko.'
$files = Adb shell ls "/sdcard/Android/data/$Pkg/files/"
if ($files -match 'usb-start.sh') {
    Say 'Phone helper USB se start kar raha hoon'
    Adb shell sh "/sdcard/Android/data/$Pkg/files/usb-start.sh" | Write-Host
} else { Write-Host 'usb-start.sh nahi mila - app mein Settings -> Phone control wizard se pair kar lena.' }

# --- 9. check ---
Say 'Result'
(Adb shell dumpsys package $Pkg) -split "`n" | Select-String versionName | Write-Host
(Adb shell ps -A -o USER,PID,NAME) -split "`n" | Select-String mika | Write-Host

Wait 'Settings -> Apps -> Jarvis -> Battery -> "Unrestricted" chuno (Samsung background apps band kar deta hai).'
Write-Host @'

DONE. Ab sirf aapka kaam:
 1) https://aistudio.google.com/apikey pe apne Google account se free key banao.
 2) Jarvis -> Settings -> Model -> Gemini -> key paste -> "Test and save". (Key chat mein kabhi mat bhejna.)
 3) "Compatibility self-check" chalao aur screenshot apne doosre Claude session ko bhejo.
'@ -ForegroundColor Green
