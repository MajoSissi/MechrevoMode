@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem 关闭 Go 模块校验（部分网络环境下 sum.golang.org 不可达）
set GOSUMDB=off
set GOFLAGS=-mod=mod

echo [1/3] 整理依赖 ...
go mod tidy
if errorlevel 1 goto :fail

echo [2/3] 编译（无控制台窗口）...
go build -trimpath -ldflags "-s -w -H windowsgui" -o MechrevoMode.exe .
if errorlevel 1 goto :fail

echo [3/3] 完成
for %%A in (MechrevoMode.exe) do echo     输出: %%~fA  (%%~zA 字节)
goto :eof

:fail
echo.
echo 编译失败，请检查上面的错误信息。
exit /b 1
