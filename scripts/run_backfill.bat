@echo off
chcp 65001 > nul
setlocal

cd /d D:\autoNote

set PYTHONPATH=program/src;program/libs/google-docs-client/src;program/libs/telegram-client/src;program/libs/llm-gateway/src;program/services/analyzer/src;program/services/collector/src;program/services/merger/src;program/services/reviewer/src;program/services/writer/src
set PYTHONIOENCODING=utf-8

echo autoNote 백필 실행 중... (from-id=%1, to-id=%2)
python program/src/main.py --mode backfill --from-id %1 --to-id %2

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] 백필 실행 실패! ErrorCode: %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)

echo 백필 완료.
endlocal
