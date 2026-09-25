@echo off
chcp 65001 > nul
set SYS=D:\User\OneDrive\Programm\RyzenAdj\WinRing0x64.sys
echo ==== 1) create service ====
sc create WinRing0_1_2_0 type= kernel start= demand binPath= "%SYS%"
echo create_rc=%errorlevel%
echo.
echo ==== 2) start service ====
sc start WinRing0_1_2_0
echo start_rc=%errorlevel%
echo.
echo ==== 3) query ====
sc query WinRing0_1_2_0
echo.
echo ==== 4) if running, try ryzenadj ====
cd /d "D:\User\OneDrive\Programm\RyzenAdj"
ryzenadj.exe --dump-table
echo ryzenadj_rc=%errorlevel%
