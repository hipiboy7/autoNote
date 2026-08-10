@echo off
chcp 65001 > nul
setlocal

cd /d D:\autoNote

set PYTHONIOENCODING=utf-8

echo autoNote 배치 상태 점검 중...
python D:\autoNote\cli\check_batch_health.py
set RC=%ERRORLEVEL%

rem 0=정상 1=이상 알림 발송 2=오늘 이미 알림 3=점검 실패
if %RC% EQU 0 echo [OK] 배치 정상
rem 1 은 "알림 발송 성공"과 "발송 실패" 를 함께 쓴다. 구분은 위 [Watchdog] 줄에 있다.
if %RC% EQU 1 echo [ALERT] 이상 감지 - 위 [Watchdog] 줄에서 알림 발송 여부를 확인하십시오
if %RC% EQU 2 echo [ALERT] 이상 상태 - 오늘 이미 알림을 보냈습니다
if %RC% EQU 3 echo [ERROR] 점검 자체가 실패했습니다

rem 종료 코드를 그대로 넘긴다.
rem echo 는 ERRORLEVEL 을 0 으로 덮어쓴다. 이 줄이 없으면 스케줄러 이력에
rem 언제나 "0(정상)" 으로 남아 감시 이력 자체가 쓸모없어진다.
rem 실제로 2026-08-08~10 배치 3일 실패 동안 이력은 계속 0 이었다.
endlocal & exit /b %RC%
