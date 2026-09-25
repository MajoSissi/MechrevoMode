$out = @()
function W($s) { $script:out += $s }

W '=== 1. GCU/CCU/AiStone 相关进程（含路径与命令行）==='
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match 'GCU|CCU|Systray|THRM|AiStone|Mechrevo' } |
  ForEach-Object {
    W ("  Name        = " + $_.Name)
    W ("    PID       = " + $_.ProcessId)
    W ("    Path      = " + $_.ExecutablePath)
    W ("    CommandL  = " + $_.CommandLine)
    W ''
  }

W '=== 2. GCUBridge 服务详情 ==='
$s = Get-CimInstance Win32_Service -Filter "Name='GCUBridge'" -ErrorAction SilentlyContinue
if ($s) {
  W ("  State      = " + $s.State + "   StartMode = " + $s.StartMode)
  W ("  StartName  = " + $s.StartName)
  W ("  PathName   = " + $s.PathName)
  W ("  DelayedAutoStart = " + $s.DelayedAutoStart)
}
W ''
W '=== 3. 所有「自动启动但不是运行中」的服务 ==='
Get-CimInstance Win32_Service -ErrorAction SilentlyContinue |
  Where-Object { $_.StartMode -eq 'Auto' -and $_.State -ne 'Running' } |
  ForEach-Object { W ("  " + $_.Name + "  [" + $_.State + "]  " + $_.PathName) }
W ''
W '=== 4. D:\Tools\L-Mechrevo 目录 ==='
if (Test-Path 'D:\Tools\L-Mechrevo') {
  Get-ChildItem 'D:\Tools\L-Mechrevo' -Recurse -File -ErrorAction SilentlyContinue |
    Select-Object -First 60 | ForEach-Object { W ("  " + $_.FullName) }
} else { W '  (不存在)' }
W ''
W '=== 5. CCU 相关 Appx 包 ==='
Get-AppxPackage -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'CCU|Mechrevo|AiStone' } |
  ForEach-Object {
    W ("  Name        = " + $_.Name)
    W ("    FullName  = " + $_.PackageFullName)
    W ("    Location  = " + $_.InstallLocation)
    W ''
  }
W ''
W '=== 6. 计划任务（全部里含 CCU/AiStone/Mechrevo/机械）==='
try {
  Get-ScheduledTask -ErrorAction Stop |
    Where-Object { $_.TaskName -match 'CCU|AiStone|Mechrevo|机械' -or ($_.Actions.Execute -join ' ') -match 'CCU|AiStone|Mechrevo' } |
    ForEach-Object {
      W ("  " + $_.TaskPath + $_.TaskName + "  State=" + $_.State)
      W ("    Exec = " + (($_.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join ' | '))
    }
} catch { W ('  失败: ' + $_.Exception.Message) }
W ''
W '=== 7. 启动文件夹 ==='
$sf = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\StartUp'
$sf2 = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
foreach ($d in @($sf, $sf2)) {
  W ("  [" + $d + "]")
  if (Test-Path $d) { Get-ChildItem $d -Force | ForEach-Object { W ("    " + $_.Name) } } else { W '    (不存在)' }
}
$out | Out-File 'C:\Windows\Temp\mm_gcu2.txt' -Encoding UTF8
Write-Output 'ok'
