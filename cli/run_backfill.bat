@echo off
chcp 65001 > nul
setlocal

cd /d D:\autoNote

set PYTHONIOENCODING=utf-8

echo autoNote 백필 실행 중... (from-id=%1, to-id=%2)
python -m autonote.main --mode backfill --from-id %1 --to-id %2

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] 백필 실행 실패! ErrorCode: %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)

echo 백필 완료.
endlocal
