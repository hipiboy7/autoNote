@echo off
chcp 65001 > nul
setlocal

cd /d D:\autoNote

set PYTHONPATH=program/src;program/libs/agy-gateway/src;program/libs/google-docs-client/src;program/libs/telegram-client/src;program/libs/llm-gateway/src;program/services/analyzer/src;program/services/collector/src;program/services/merger/src;program/services/reviewer/src;program/services/writer/src
set PYTHONIOENCODING=utf-8

echo autoNote 하이브리드 배치 실행 중...
python program/src/main.py --mode batch

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] main.py 배치 실행 실패! ErrorCode: %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)

echo Google Drive 동기화 실행 중...
python D:\autoNote\scripts\sync_to_drive.py

endlocal


