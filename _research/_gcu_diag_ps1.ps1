$ErrorActionPreference = 'SilentlyContinue'
# 提权运行（走 _elev.py）。输出纯 ASCII 字段，避免编码问题。
"=== 13688 端口的监听者 ==="
Get-NetTCPConnection -LocalPort 13688 -State Listen | ForEach-Object {
  $p = Get-Process -Id $_.OwningProcess
  "  {0}  pid={1}  proc={2}" -f $_.LocalAddress, $_.OwningProcess, $p.ProcessName
  "     path=" + $p.Path
}
"=== GCU 相关进程 ==="
Get-CimInstance Win32_Process -Filter "Name='GCUService.exe' OR Name='GCUBridge.exe' OR Name='SystrayComponent.exe' OR Name='sysConUtil.exe'" |
  ForEach-Object { "  pid={0} session={1} name={2}`n     path={3}" -f $_.ProcessId, $_.SessionId, $_.Name, $_.ExecutablePath }
"=== 服务 GCUBridge ==="
Get-Service GCUBridge | ForEach-Object { "  Status={0} StartType={1}" -f $_.Status, $_.StartType }
"=== GCUBridge 服务的恢复设置 (FailureActions) ==="
$out = & sc.exe qfailure GCUBridge 2>&1
$out | ForEach-Object { "  " + $_ }
"=== 文件是否存在 ==="
@(
  'D:\Tools\L-Mechrevo\GCU\AiStoneService\GCUBridge.exe',
  'D:\Tools\L-Mechrevo\GCU\AiStoneService\MyControlCenter\GCUService.exe',
  'C:\Program Files\OEM\机械革命控制中心\AiStoneService\MyControlCenter\GCUService.exe',
  'C:\Program Files\OEM\机械革命控制中心\AiStoneService\GCUBridge.exe'
) | ForEach-Object { "  [{0}] {1}" -f (Test-Path $_), $_ }
