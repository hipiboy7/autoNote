@echo off
chcp 65001 > nul
setlocal

cd /d D:\autoNote

set PYTHONIOENCODING=utf-8

echo autoNote 하이브리드 배치 실행 중...
python -m autonote.main --mode batch

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] main.py 배치 실행 실패! ErrorCode: %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)

echo Google Drive 동기화 실행 중...
python D:\autoNote\ops\tools\sync_to_drive.py

endlocal


