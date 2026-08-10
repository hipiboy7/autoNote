@echo off
chcp 65001 > nul
title [autoNote] Antigravity - 투자 리서치 노트
cd /d "D:\autoNote"
echo ============================================
echo   투자 리서치 노트 - Antigravity 시작
echo   디렉터리: D:\autoNote
echo   모델: settings.json 기본값 (Gemini 3.5 Flash (High))
echo ============================================
echo.
start /b python D:\autoNote\ops\tools\sync_to_drive.py
"C:\Users\SYSADMIN\AppData\Local\agy\bin\agy.exe" --dangerously-skip-permissions
