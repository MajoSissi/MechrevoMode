@echo off
echo ==== WinRing0 / inpout service ====
sc query WinRing0_1_2_0 2>&1 | findstr /C:"SERVICE_NAME" /C:"STATE"
sc query inpoutx64 2>&1 | findstr /C:"SERVICE_NAME" /C:"STATE"
sc query RYZENADJ 2>&1 | findstr /C:"SERVICE_NAME" /C:"STATE"
echo.
echo ==== vulnerable driver blocklist ====
reg query "HKLM\SYSTEM\CurrentControlSet\Control\CI\Config" /v VulnerableDriverBlocklistEnable 2>&1
echo.
echo ==== HVCI / memory integrity ====
reg query "HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity" /v Enabled 2>&1
reg query "HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard" /v EnableVirtualizationBasedSecurity 2>&1
echo.
echo ==== AMD RyzenAdj task ====
schtasks /query /tn "AMD\RyzenAdj" 2>&1 | findstr /C:"TaskName" /C:"Status"
echo.
echo ==== copy ryzenadj to local disk and retry ====
mkdir "C:\MMTest" 2>nul
copy /y "D:\User\OneDrive\Programm\RyzenAdj\*.*" "C:\MMTest\" >nul 2>&1
cd /d "C:\MMTest"
ryzenadj.exe --set-coall=-25
echo local-copy errorlevel=%errorlevel%
