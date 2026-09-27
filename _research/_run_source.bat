@echo off
rem 提权启动源码目录里那份 exe（带 RUNASADMIN 兼容性标记，必须提权才起得来），
rem 用 start 脱离，免得 cmd 一直等着它退出。
taskkill /F /IM MechrevoMode.exe > nul 2>&1
timeout /t 2 /nobreak > nul
start "" "D:\User\Desktop\MechrevoMode\MechrevoMode.exe"
