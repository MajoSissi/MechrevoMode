@echo off
chcp 65001 > nul
echo ==== run ryzenadj (WinRing0 folder) ====
cd /d "C:\MMB"
ryzenadj.exe --dump-table
echo errorlevel=%errorlevel%
echo.

echo ==== WinRing0 service right after ====
sc query WinRing0_1_2_0 2>&1 | findstr /C:"SERVICE_NAME" /C:"STATE" /C:"FAILED" /C:"1060"
echo.

echo ==== CodeIntegrity operational log (driver blocklist) ====
wevtutil qe Microsoft-Windows-CodeIntegrity/Operational /c:30 /rd:true /f:text 2>&1 | findstr /I /C:"WinRing" /C:"inpout" /C:"blocked" /C:"Event ID"
echo.

echo ==== System log: driver load failures ====
wevtutil qe System /q:"*[System[Provider[@Name='Microsoft-Windows-Kernel-PnP'] or Provider[@Name='Service Control Manager']]]" /c:40 /rd:true /f:text 2>&1 | findstr /I /C:"WinRing" /C:"inpout" /C:"7045" /C:"7000" /C:"7026"
echo.

echo ==== SCM registry entries for these drivers ====
reg query "HKLM\SYSTEM\CurrentControlSet\Services\WinRing0_1_2_0" 2>&1 | findstr /I /C:"ImagePath" /C:"Start" /C:"Type" /C:"error"
reg query "HKLM\SYSTEM\CurrentControlSet\Services\inpoutx64" 2>&1 | findstr /I /C:"ImagePath" /C:"Start" /C:"Type"
