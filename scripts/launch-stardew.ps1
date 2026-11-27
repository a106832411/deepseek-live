# 启动星露谷（SMAPI）并把窗口固定到屏幕左上角；顺带保证不因失焦暂停
param(
    [string]$GamePath = 'D:\steam\steamapps\common\Stardew Valley',
    [int]$X = 0,
    [int]$Y = 0
)

$ErrorActionPreference = 'Stop'

$code = @'
using System;
using System.Runtime.InteropServices;
public class WinMove {
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);
    public delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
    public struct RECT { public int Left, Top, Right, Bottom; }

    public static IntPtr FindStardew(uint pid) {
        IntPtr found = IntPtr.Zero;
        EnumWindows((h, l) => {
            uint p; GetWindowThreadProcessId(h, out p);
            if (p == pid) {
                var sb = new System.Text.StringBuilder(256);
                GetWindowText(h, sb, 256);
                if (sb.ToString().Contains("Stardew")) { found = h; return false; }
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
'@

if (-not ('WinMove' -as [type])) { Add-Type -TypeDefinition $code | Out-Null }

$exe = Join-Path $GamePath 'StardewModdingAPI.exe'
if (-not (Test-Path $exe)) { throw "找不到 $exe" }

$proc = Get-Process StardewModdingAPI -ErrorAction SilentlyContinue
if (-not $proc) {
    Start-Process -FilePath $exe -WorkingDirectory $GamePath `
        -RedirectStandardOutput 'D:\live\logs\smapi-overnight.out.log' `
        -RedirectStandardError 'D:\live\logs\smapi-overnight.err.log'
    Write-Host "已启动 SMAPI，等待窗口…"
    Start-Sleep -Seconds 20
    $proc = Get-Process StardewModdingAPI -ErrorAction SilentlyContinue
}

if (-not $proc) { throw "SMAPI 没起来" }
$pid0 = $proc[0].Id

$h = [IntPtr]::Zero
for ($i = 0; $i -lt 40; $i++) {
    $h = [WinMove]::FindStardew([uint32]$pid0)
    if ($h -ne [IntPtr]::Zero) { break }
    Start-Sleep -Milliseconds 500
}
if ($h -eq [IntPtr]::Zero) { throw "找不到游戏窗口" }

$r = New-Object WinMove+RECT
[WinMove]::GetWindowRect($h, [ref]$r) | Out-Null
$w = $r.Right - $r.Left
$hh = $r.Bottom - $r.Top
# SWP_NOZORDER(4) | SWP_SHOWWINDOW(0x40)
[WinMove]::SetWindowPos($h, [IntPtr]::Zero, $X, $Y, $w, $hh, 0x44) | Out-Null
[WinMove]::SetForegroundWindow($h) | Out-Null
Write-Host ("窗口已挪到 ({0},{1})，尺寸 {2}x{3}" -f $X, $Y, $w, $hh)
