@echo off
rem Elevated deploy: stop the old instance and copy the fresh build into the installed
rem directory. Deliberately does NOT start anything here.
rem
rem Why: a process started with `start` from this script ends up inside the elevated
rem helper's process tree and gets reaped when that helper exits -- but not before it
rem has grabbed the single-instance mutex. The instance launched afterwards by
rem `schtasks /Run` then loses the mutex race and disappears, which looks exactly like
rem "the app crashes silently 30 seconds after launch" (cost an hour to chase down).
rem So: copy here, start via the scheduled task in a separate step.
rem Keep this file pure ASCII.
setlocal

set SRC=D:\User\Desktop\MechrevoMode\MechrevoMode.exe
set DST=D:\User\OneDrive\Programm\MechrevoMode\MechrevoMode.exe

taskkill /F /IM MechrevoMode.exe > nul 2>&1
timeout /t 3 /nobreak > nul

copy /Y "%SRC%" "%DST%"
if errorlevel 1 (
  echo COPY FAILED
  exit /b 1
)
echo COPIED
for %%A in ("%DST%") do echo DST_SIZE=%%~zA
for %%A in ("%SRC%") do echo SRC_SIZE=%%~zA
exit /b 0
