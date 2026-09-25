@echo off
chcp 65001 > nul
echo ==== CPU ====
reg query "HKLM\HARDWARE\DESCRIPTION\System\CentralProcessor\0" /v ProcessorNameString 2>&1 | findstr ProcessorNameString
reg query "HKLM\HARDWARE\DESCRIPTION\System\CentralProcessor\0" /v Identifier 2>&1 | findstr Identifier
echo.

set SRC=D:\User\OneDrive\Programm\RyzenAdj
rmdir /s /q "C:\MMA" 2>nul
rmdir /s /q "C:\MMB" 2>nul
mkdir "C:\MMA" 2>nul
mkdir "C:\MMB" 2>nul

echo ==== A: only inpoutx64 (no WinRing0) ====
copy /y "%SRC%\ryzenadj.exe" "C:\MMA\" >nul
copy /y "%SRC%\libryzenadj.dll" "C:\MMA\" >nul
copy /y "%SRC%\inpoutx64.dll" "C:\MMA\" >nul
cd /d "C:\MMA"
ryzenadj.exe --set-coall=-25
echo A errorlevel=%errorlevel%
echo.

echo ==== B: only WinRing0 ====
copy /y "%SRC%\ryzenadj.exe" "C:\MMB\" >nul
copy /y "%SRC%\libryzenadj.dll" "C:\MMB\" >nul
copy /y "%SRC%\WinRing0x64.dll" "C:\MMB\" >nul
copy /y "%SRC%\WinRing0x64.sys" "C:\MMB\" >nul
cd /d "C:\MMB"
ryzenadj.exe --set-coall=-25
echo B errorlevel=%errorlevel%
echo.

echo ==== C: no driver dlls at all ====
rmdir /s /q "C:\MMC" 2>nul
mkdir "C:\MMC" 2>nul
copy /y "%SRC%\ryzenadj.exe" "C:\MMC\" >nul
copy /y "%SRC%\libryzenadj.dll" "C:\MMC\" >nul
cd /d "C:\MMC"
ryzenadj.exe --set-coall=-25
echo C errorlevel=%errorlevel%
