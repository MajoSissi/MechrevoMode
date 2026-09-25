$out = @()
function W($s) { $script:out += $s }

W '=== GCUBridge 服务注册表配置 ==='
$k = 'HKLM:\SYSTEM\CurrentControlSet\Services\GCUBridge'
if (Test-Path $k) {
  $p = Get-ItemProperty $k
  W ("  Start           = " + $p.Start + "   (2=自动 3=手动 4=禁用)")
  W ("  ImagePath       = " + $p.ImagePath)
  W ("  ObjectName      = " + $p.ObjectName)
  W ("  DependOnService = " + ($p.DependOnService -join ','))
  W ("  DelayedAutostart= " + $p.DelayedAutostart)
  W ("  Type            = " + $p.Type)
  W ''
  if (Test-Path ($k + '\TriggerInfo')) {
    W '  [TriggerInfo 存在 —— 说明是触发器启动，不是纯开机自启]'
    (Get-ItemProperty ($k + '\TriggerInfo')).PSObject.Properties |
      Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { W ("    " + $_.Name) }
  } else { W '  (无 TriggerInfo)' }
  if (Test-Path ($k + '\FailureActions')) {
    W '  [FailureActions]'
  }
} else { W '  (键不存在)' }

W ''
W '=== 服务当前状态与技术信息 ==='
$s = Get-CimInstance Win32_Service -Filter "Name='GCUBridge'" -ErrorAction SilentlyContinue
if ($s) {
  W ("  State           = " + $s.State)
  W ("  StartMode       = " + $s.StartMode)
  W ("  ProcessId       = " + $s.ProcessId)
  W ("  ServiceSpecificExitCode = " + $s.ServiceSpecificExitCode)
}

W ''
W '=== 关键进程的启动时间 ==='
$procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match 'GCU|CCU|Systray|THRM|MechrevoMode' }
foreach ($p in $procs) {
  $ct = $null
  if ($p.CreationDate) { $ct = ([Management.ManagementDateTimeConverter]::ToDateTime($p.CreationDate)) }
  W ("  {0,-24} pid={1,-7} 启动于 {2}" -f $p.Name, $p.ProcessId, $ct)
}

W ''
W '=== 开机时间 / 当前时间 ==='
$os = Get-CimInstance Win32_OperatingSystem
W ("  LastBootUpTime = " + $os.LastBootUpTime)
W ("  Now            = " + (Get-Date))

W ''
W '=== System 日志里 GCUBridge 服务相关事件（本次开机以来）==='
try {
  $svcEv = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Service Control Manager'; StartTime=$os.LastBootUpTime} -ErrorAction Stop |
    Where-Object { $_.Message -match 'GCUBridge' }
  if ($svcEv) { foreach ($e in $svcEv) { W ("  [" + $e.TimeCreated + "] id=" + $e.Id + " " + ($e.Message -replace "`r`n", ' ')) } }
  else { W '  (无 GCUBridge 相关事件)' }
} catch { W ('  读取失败: ' + $_.Exception.Message) }

$out | Out-File "$env:TEMP\mm_gcu3.txt" -Encoding UTF8
Write-Output 'ok'
