@echo off
rem Diagnose why MechrevoMode.exe vanished from the installed directory.
rem Pure ASCII only.
setlocal

set SRC=D:\User\Desktop\MechrevoMode\MechrevoMode.exe
set DST=D:\User\OneDrive\Programm\MechrevoMode\MechrevoMode.exe

echo === before copy ===
dir /a "D:\User\OneDrive\Programm\MechrevoMode"

echo.
echo === defender threats ===
powershell -NoProfile -Command "Get-MpThreatDetection | Select-Object -Last 5 | Format-List ThreatID,InitialDetectionTime,Resources,ActionSuccess; Get-MpThreat | Select-Object -Last 5 | Format-List ThreatName,Resources" 2>&1

echo.
echo === copy back ===
copy /Y "%SRC%" "%DST%"
if errorlevel 1 (echo COPY FAILED) else (echo COPIED)

echo.
echo === after copy ===
dir /a "D:\User\OneDrive\Programm\MechrevoMode"
exit /b 0
