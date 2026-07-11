@echo off
chcp 65001 > nul
setlocal

cd /d "D:\autoNote"
"C:\Users\SYSADMIN\AppData\Local\agy\bin\agy.exe" --print "일일 배치" --dangerously-skip-permissions

endlocal
