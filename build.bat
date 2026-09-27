@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem NOTE: keep this file pure ASCII. cmd.exe mis-parses non-ASCII bytes in .bat
rem files under some code pages (notably 65001) and silently corrupts the lines
rem that follow -- including ASCII ones. Chinese docs live in README.md instead.

rem NOTE: every 'go' call needs 'call'. Tools like mise/asdf put a go.cmd shim
rem on PATH; invoking a .cmd from a .bat WITHOUT 'call' transfers control away
rem and never comes back, so the rest of this script silently never runs.

rem Disable the Go module checksum DB (sum.golang.org may be unreachable).
set GOSUMDB=off
set GOFLAGS=-mod=mod

set OUTDIR=build
set OUTEXE=%OUTDIR%\MechrevoMode.exe

echo [1/4] Checking icon resource ...
if not exist "rsrc_windows_amd64.syso" (
  echo       WARN: rsrc_windows_amd64.syso missing -- the exe will have no icon.
  echo       Run img\make-icon.bat to regenerate it from img\logo.ico.
) else (
  echo       Using rsrc_windows_amd64.syso, built from img\logo.ico
)

echo [2/4] Preparing output dir %OUTDIR% ...
if not exist "%OUTDIR%" mkdir "%OUTDIR%"
if errorlevel 1 goto :fail

echo [3/4] go mod tidy ...
call go mod tidy
if errorlevel 1 goto :fail

echo [4/4] Building, no console window ...
call go build -trimpath -ldflags "-s -w -H windowsgui" -o "%OUTEXE%" .
if errorlevel 1 goto :fail

for %%A in ("%OUTEXE%") do echo       Output: %%~fA  [%%~zA bytes]
goto :eof

:fail
echo.
echo Build FAILED -- check the errors above.
exit /b 1
