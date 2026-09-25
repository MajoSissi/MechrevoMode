@echo off
chcp 65001 > nul
echo ==== CodeIntegrity latest 2 events (full text) ====
wevtutil qe Microsoft-Windows-CodeIntegrity/Operational /c:2 /rd:true /f:text 2>&1
echo.
echo ==== System 7045 / 7000 latest (full text) ====
wevtutil qe System /q:"*[System[(EventID=7045 or EventID=7000 or EventID=7026)]]" /c:6 /rd:true /f:text 2>&1
