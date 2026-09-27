@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0.."

rem Regenerate rsrc_windows_amd64.syso from img\logo.ico.
rem The Go linker auto-embeds any *.syso in the main package directory, so
rem running this once after replacing img\logo.ico is enough -- build.bat
rem does not need it.
rem
rem Keep this file pure ASCII: cmd.exe mis-parses non-ASCII bytes in .bat
rem files under some code pages and corrupts the following lines.

set GOSUMDB=off

set RSRC=
for /f "delims=" %%P in ('where rsrc 2^>nul') do if not defined RSRC set RSRC=%%P
if not defined RSRC if exist "%GOBIN%\rsrc.exe" set RSRC=%GOBIN%\rsrc.exe

if not defined RSRC goto :gorun

echo Using: %RSRC%
"%RSRC%" -arch amd64 -ico img\logo.ico -o rsrc_windows_amd64.syso
goto :check

:gorun
echo rsrc not found on PATH, falling back to: go run github.com/akavel/rsrc@latest
call go run github.com/akavel/rsrc@latest -arch amd64 -ico img\logo.ico -o rsrc_windows_amd64.syso

:check
if errorlevel 1 goto :fail
for %%A in (rsrc_windows_amd64.syso) do echo       Output: %%~fA  [%%~zA bytes]
goto :eof

:fail
echo.
echo FAILED. Install rsrc first: go install github.com/akavel/rsrc@latest
exit /b 1
