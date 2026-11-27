# 状态看门狗：state 超过 180 秒没更新就把游戏窗口拉到前台（防止失焦暂停）
param(
    [int]$StaleSec = 180,
    [int]$IntervalSec = 60
)

$code = @'
using System;
using System.Runtime.InteropServices;
public class Wd {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);
    public delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
    public static IntPtr Find(uint pid) {
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
if (-not ('Wd' -as [type])) { Add-Type -TypeDefinition $code | Out-Null }

$state = 'D:\steam\steamapps\common\Stardew Valley\Mods\WhalePlayer\player_state.json'
$log = 'D:\live\logs\state-watchdog.log'

while ($true) {
    try {
        $proc = Get-Process StardewModdingAPI -ErrorAction SilentlyContinue
        if (-not $proc) {
            Add-Content -Path $log -Value ("[{0}] 游戏没在跑" -f (Get-Date -Format 'HH:mm:ss'))
        } else {
            $age = ((Get-Date) - (Get-Item $state).LastWriteTime).TotalSeconds
            if ($age -gt $StaleSec) {
                $h = [Wd]::Find([uint32]$proc[0].Id)
                if ($h -ne [IntPtr]::Zero) {
                    [Wd]::SetForegroundWindow($h) | Out-Null
                }
                Add-Content -Path $log -Value ("[{0}] 状态 {1:N0}s 未更新 → 把游戏窗口拉回前台" -f (Get-Date -Format 'HH:mm:ss'), $age)
            }
        }
    } catch {
        Add-Content -Path $log -Value ("[{0}] 看门狗异常: {1}" -f (Get-Date -Format 'HH:mm:ss'), $_.Exception.Message)
    }
    Start-Sleep -Seconds $IntervalSec
}
