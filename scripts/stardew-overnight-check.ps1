# 夜间测试巡检：汇总 agent / 游戏状态 / 日志错误，输出一段可读报告
param(
    [string]$AgentLog = 'D:\live\logs\agent-overnight.out.log',
    [string]$AgentErr = 'D:\live\logs\agent-overnight.err.log',
    [string]$StateFile = 'D:\steam\steamapps\common\Stardew Valley\Mods\WhalePlayer\player_state.json',
    [string]$SmapiLog = "$env:APPDATA\StardewValley\ErrorLogs\SMAPI-latest.txt"
)

function L([string]$s) { $s }

$now = Get-Date -Format 'HH:mm:ss'
L "== [$now] 巡检 =="

# agent 进程
$agents = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
          Where-Object { $_.CommandLine -match 'stardew_agent' }
L ("agent 进程: " + ($agents | Measure-Object).Count)

# 游戏状态
if (Test-Path $StateFile) {
    $st = Get-Item $StateFile
    $age = [Math]::Round(((Get-Date) - $st.LastWriteTime).TotalSeconds, 1)
    try {
        $d = Get-Content $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        L ("游戏: 第 $($d.day) 天 $($d.time) @ $($d.location) | 状态年龄 ${age}s | 金币 $($d.player.money)")
    } catch {
        L ("游戏: 状态文件解析失败 | 年龄 ${age}s")
    }
} else {
    L "游戏: 找不到 player_state.json"
}

# agent 日志尾部 + 错误
if (Test-Path $AgentLog) {
    $tail = Get-Content $AgentLog -Tail 6 -Encoding UTF8 -ErrorAction SilentlyContinue
    L "--- agent 最近 ---"
    $tail | ForEach-Object { L ("  " + $_) }
    $errs = Select-String -Path $AgentLog -Pattern 'Traceback|ERROR|异常|失败' -Encoding UTF8 -ErrorAction SilentlyContinue |
            Select-Object -Last 3
    if ($errs) { L "--- agent 错误(最近3) ---"; $errs | ForEach-Object { L ("  " + $_.Line.Trim()) } }
}
if (Test-Path $AgentErr) {
    $e = Get-Item $AgentErr
    if ($e.Length -gt 0) { L ("agent stderr 大小: " + $e.Length) }
}

# SMAPI 错误
if (Test-Path $SmapiLog) {
    $serr = Select-String -Path $SmapiLog -Pattern '\[ERROR\]' -Encoding UTF8 -ErrorAction SilentlyContinue |
            Select-Object -Last 5
    if ($serr) { L "--- SMAPI 错误(最近5) ---"; $serr | ForEach-Object { L ("  " + $_.Line.Trim()) } }
    else { L "SMAPI: 无 ERROR" }
}

# git
Push-Location 'D:\live\deepseek-live'
$gs = git status --short 2>$null
if ($gs) { L "--- git 未提交 ---"; $gs | Select-Object -First 6 | ForEach-Object { L ("  " + $_) } } else { L "git: 干净" }
Pop-Location
