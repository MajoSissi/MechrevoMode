@echo off
cd /d "D:\User\OneDrive\Programm\RyzenAdj"
echo ==== 1) --set-coall=-25 ====
ryzenadj.exe --set-coall=-25
echo errorlevel=%errorlevel%
echo.
echo ==== 2) -i (dump info) ====
ryzenadj.exe -i
echo errorlevel=%errorlevel%
echo.
echo ==== 3) no args ====
ryzenadj.exe
echo errorlevel=%errorlevel%
