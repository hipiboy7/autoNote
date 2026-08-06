@echo off
chcp 65001 > nul
setlocal

cd /d D:\autoNote

set PYTHONIOENCODING=utf-8

echo autoNote 배치 상태 점검 중...
python D:\autoNote\scripts\check_batch_health.py

rem 0=정상 1=이상 알림 발송 2=오늘 이미 알림 3=점검 실패
if %ERRORLEVEL% EQU 0 echo [OK] 배치 정상
if %ERRORLEVEL% EQU 1 echo [ALERT] 이상 감지 - 알림을 발송했습니다
if %ERRORLEVEL% EQU 2 echo [ALERT] 이상 상태 - 오늘 이미 알림을 보냈습니다
if %ERRORLEVEL% EQU 3 echo [ERROR] 점검 자체가 실패했습니다

endlocal
