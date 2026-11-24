# 星露谷窗口守护：游戏（重新）启动后把窗口钉到屏幕左上角 (0,0)
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File scripts\watch-stardew-window.ps1
# 逻辑：轮询游戏窗口句柄；句柄一变（= 游戏重启/新开）就 SetWindowPos 到左上角，
#       并在 +2s / +6s 再补两次，防止游戏启动时自己把窗口挪走。
param(
  [int]$PollMs = 1500,
  [int]$X = 0,
  [int]$Y = 0
)

$ErrorActionPreference = 'Stop'
$LogDir = 'D:\live\logs'
$LogFile = Join-Path $LogDir 'stardew-window.log'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public struct RECT { public int Left, Top, Right, Bottom; }
public class StardewWin {
  [DllImport("user32.dll", SetLastError = true)] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr after, int X, int Y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr hWnd);
}
'@

[StardewWin]::SetProcessDPIAware() | Out-Null

$SWP_NOSIZE       = 0x0001
$SWP_NOZORDER     = 0x0004
$SWP_NOACTIVATE   = 0x0010
$SWP_SHOWWINDOW   = 0x0040

function Write-Log([string]$msg) {
  $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
  Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Get-GameWindow {
  foreach ($n in @('StardewModdingAPI', 'Stardew Valley')) {
    $p = Get-Process -Name $n -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
    if ($p) { return $p }
  }
  return $null
}

function Move-ToCorner([IntPtr]$hwnd) {
  $r = New-Object RECT
  [StardewWin]::GetWindowRect($hwnd, [ref]$r) | Out-Null
  $w = $r.Right - $r.Left
  $h = $r.Bottom - $r.Top
  $ok = [StardewWin]::SetWindowPos($hwnd, [IntPtr]::Zero, $X, $Y, 0, 0,
    $SWP_NOSIZE -bor $SWP_NOZORDER -bor $SWP_NOACTIVATE -bor $SWP_SHOWWINDOW)
  $r2 = New-Object RECT
  [StardewWin]::GetWindowRect($hwnd, [ref]$r2) | Out-Null
  Write-Log ("move hwnd={0} {1}x{2}  {3},{4} -> {5},{6}  ok={7}" -f $hwnd, $w, $h, $r.Left, $r.Top, $r2.Left, $r2.Top, $ok)
}

Write-Log "watcher start (poll=${PollMs}ms target=$X,$Y)"
$lastHwnd = [IntPtr]::Zero
$pending  = @{}   # hwnd -> 剩余补位次数

while ($true) {
  try {
    $p = Get-GameWindow
    $hwnd = if ($p) { $p.MainWindowHandle } else { [IntPtr]::Zero }

    if ($hwnd -ne [IntPtr]::Zero -and $hwnd -ne $lastHwnd) {
      # 新窗口（游戏刚启动/重启）：等它自己 settle 一下再挪
      Write-Log "new game window hwnd=$hwnd pid=$($p.Id)"
      Start-Sleep -Milliseconds 2500
      Move-ToCorner $hwnd
      # 游戏重启后 OBS 的窗口采集会指向旧窗口标题（标题里带 mod 数量），这里顺手重指一次
      try {
        $repoint = Join-Path $PSScriptRoot 'obs-repoint-window.ps1'
        if (Test-Path $repoint) {
          Start-Process -FilePath 'powershell' -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',$repoint -WindowStyle Hidden
          Write-Log 'obs repoint scheduled'
        }
      } catch {
        Write-Log ('obs repoint failed: ' + $_.Exception.Message)
      }
      $pending[$hwnd] = 2      # 2 秒后再补一次，6 秒后再补一次
      $lastHwnd = $hwnd
    } elseif ($hwnd -eq [IntPtr]::Zero -and $lastHwnd -ne [IntPtr]::Zero) {
      Write-Log "game window closed (was $lastHwnd)"
      $pending.Remove($lastHwnd) | Out-Null
      $lastHwnd = [IntPtr]::Zero
    } elseif ($hwnd -ne [IntPtr]::Zero -and $pending.ContainsKey($hwnd)) {
      Move-ToCorner $hwnd
      $pending[$hwnd] = $pending[$hwnd] - 1
      if ($pending[$hwnd] -le 0) { $pending.Remove($hwnd) | Out-Null }
    }
  } catch {
    Write-Log ("error: " + $_.Exception.Message)
    Start-Sleep -Seconds 5
  }
  Start-Sleep -Milliseconds $PollMs
}
