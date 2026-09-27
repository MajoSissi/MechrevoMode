tasklist /FI "IMAGENAME eq GCUService.exe"
start "" "D:\Tools\L-Mechrevo\GCU\AiStoneService\MyControlCenter\GCUService.exe"
timeout /t 20 /nobreak > nul
tasklist /FI "IMAGENAME eq GCUService.exe"
