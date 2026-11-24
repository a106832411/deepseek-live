# OBS 窗口采集自动重指：游戏（重新）启动后，把 OBS「窗口采集」源的窗口串更新成当前窗口。
# 背景：星露谷窗口标题带 mod 数量（"...with 12 mods"），启停 mod 会变标题，OBS 就对不上黑屏。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File scripts\obs-repoint-window.ps1
param(
    [string]$EnvFile = (Join-Path $PSScriptRoot '..\.env'),
    [string]$LogFile = 'D:\live\logs\obs-repoint.log'
)

$ErrorActionPreference = 'Stop'

function Write-Log([string]$msg) {
    $dir = Split-Path -Parent $LogFile
    if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Add-Content -Path $LogFile -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg) -Encoding UTF8
}

# 读 .env
$cfg = @{ host = '127.0.0.1'; port = 4455; password = '' }
if (Test-Path $EnvFile) {
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match '^\s*DEEPLIVE_OBS_HOST\s*=\s*(.+)$') { $cfg.host = $Matches[1].Trim() }
        elseif ($line -match '^\s*DEEPLIVE_OBS_PORT\s*=\s*(.+)$') { $cfg.port = [int]$Matches[1].Trim() }
        elseif ($line -match '^\s*DEEPLIVE_OBS_PASSWORD\s*=\s*(.+)$') { $cfg.password = $Matches[1].Trim() }
    }
}

# 当前游戏窗口标题
$win = Get-Process StardewModdingAPI -ErrorAction SilentlyContinue |
       Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -match 'Stardew' } |
       Select-Object -First 1
if (-not $win) { Write-Log 'no game window, skip'; exit 0 }
$target = "$($win.MainWindowTitle):SDL_app:StardewModdingAPI.exe"
Write-Log ("target: " + $target)

# 连 obs-websocket
$ws = [System.Net.WebSockets.ClientWebSocket]::new()
try {
    $ws.ConnectAsync([Uri]("ws://{0}:{1}" -f $cfg.host, $cfg.port), [Threading.CancellationToken]::None).Wait()
} catch {
    Write-Log ('obs connect failed: ' + $_.Exception.Message); exit 1
}

function RecvMsg($s) {
    $b = New-Object byte[] 8388608
    $seg = [ArraySegment[byte]]::new($b)
    $sb = New-Object Text.StringBuilder
    do {
        $r = $s.ReceiveAsync($seg, [Threading.CancellationToken]::None).Result
        $sb.Append([Text.Encoding]::UTF8.GetString($b, 0, $r.Count)) | Out-Null
    } while (-not $r.EndOfMessage)
    $sb.ToString() | ConvertFrom-Json
}
function SendMsg($s, $o) {
    $j = $o | ConvertTo-Json -Depth 10 -Compress
    $by = [Text.Encoding]::UTF8.GetBytes($j)
    $s.SendAsync([ArraySegment[byte]]::new($by), [System.Net.WebSockets.WebSocketMessageType]::Text, $true, [Threading.CancellationToken]::None).Wait()
}

$h = RecvMsg $ws
$id = @{ op = 1; d = @{ rpcVersion = 1 } }
if ($h.d.authentication) {
    $sh = [Security.Cryptography.SHA256]::Create()
    $hb = $sh.ComputeHash([Text.Encoding]::UTF8.GetBytes($cfg.password + $h.d.authentication.salt))
    $b64 = [Convert]::ToBase64String($hb)
    $sh2 = [Security.Cryptography.SHA256]::Create()
    $hb2 = $sh2.ComputeHash([Text.Encoding]::UTF8.GetBytes($b64 + $h.d.authentication.challenge))
    $id.d.authentication = [Convert]::ToBase64String($hb2)
}
SendMsg $ws $id
$null = RecvMsg $ws

$script:n = 0
function CallObs($s, $type, $data) {
    $script:n++
    $rid = "r$($script:n)"
    $m = @{ op = 6; d = @{ requestType = $type; requestId = $rid } }
    if ($data) { $m.d.requestData = $data }
    SendMsg $s $m
    while ($true) {
        $x = RecvMsg $s
        if ($x.op -eq 7 -and $x.d.requestId -eq $rid) {
            if ($x.d.requestStatus.code -ne 100) { Write-Log ("  ERR " + $type + ": " + $x.d.requestStatus.comment) }
            return $x.d.responseData
        }
    }
}

$scenes = CallObs $ws 'GetSceneList' $null
$scene = $scenes.currentProgramSceneName
$items = CallObs $ws 'GetSceneItemList' @{ sceneName = $scene }
$changed = 0
foreach ($it in $items.sceneItems) {
    if ($it.inputKind -eq 'window_capture') {
        $null = CallObs $ws 'SetInputSettings' @{
            inputName = $it.sourceName
            inputSettings = @{ window = $target }
            overlay = $true
        }
        $null = CallObs $ws 'SetSceneItemEnabled' @{ sceneName = $scene; sceneItemId = $it.sceneItemId; sceneItemEnabled = $false }
        Start-Sleep -Milliseconds 500
        $null = CallObs $ws 'SetSceneItemEnabled' @{ sceneName = $scene; sceneItemId = $it.sceneItemId; sceneItemEnabled = $true }
        Write-Log ("repointed: " + $it.sourceName)
        $changed++
    }
}
if ($changed -eq 0) { Write-Log 'no window_capture source found' }

$ws.CloseAsync([System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure, 'bye', [Threading.CancellationToken]::None).Wait()
Write-Log 'done'
