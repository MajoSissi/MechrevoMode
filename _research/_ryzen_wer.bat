@echo off
chcp 65001 > nul
echo ==== Application Error events mentioning ryzenadj ====
wevtutil qe Application /q:"*[System[Provider[@Name='Application Error']]]" /c:40 /rd:true /f:text 2>&1 | findstr /I /C:"ryzenadj" /C:"Faulting module" /C:"Exception code" /C:"Faulting application"
echo.
echo ==== WER ReportArchive ====
dir /b /o-d "%LOCALAPPDATA%\Microsoft\Windows\WER\ReportArchive" 2>&1 | findstr /I ryzenadj
echo.
echo ==== done ====
