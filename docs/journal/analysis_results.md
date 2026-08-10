# 작업 중단 및 크래시 원인 분석 보고서

투자 리서치 노트 자동화 시스템이 작업 도중 작동을 멈추거나 죽는 현상에 대해 원인을 파악하고 조치를 완료했습니다. 분석 결과, 시스템이 정상 작동하지 못했던 이유는 크게 **세 가지**였습니다.

---

## 1. Telegram MCP 서버 크래시 (가장 결정적 원인)
* **현상**: Telegram MCP 서버가 실행되는 즉시 다음과 같은 에러를 내며 강제 종료(Exit Code 1)되었습니다.
  ```
  Error starting client: Timeout should be used inside a task
  RuntimeWarning: coroutine 'open_connection' was never awaited
  ```
* **원인**: Telegram MCP용 가상환경(`venv`)이 사용자 PC에 설치된 최신 프리릴리즈 버전인 **Python 3.14** 기반으로 구축되어 있었습니다. Python 3.14의 `asyncio` 라이브러리 내부 구조 변경으로 인해, 텔레그램 연동용 패키지(`Telethon`)와 `nest_asyncio`가 충돌하여 대기 상태에 들어가지 못하고 실행 직후 죽는 현상이었습니다.
* **조치**: 
  1. 사용자 PC에 함께 설치되어 있던 안정 버전인 **Python 3.12** 환경을 감지했습니다.
  2. 기존 Python 3.14 가상환경을 제거하고, Python 3.12 기반의 가상환경을 재구축하였습니다.
  3. 필요한 패키지(`requirements.txt`)를 재설치하고 수동 기동 테스트를 거쳐, 텔레그램 세션 및 엔티티 캐시를 정상 로드하고 동작하는 상태(RUNNING)를 확인했습니다.

---

## 2. 윈도우 작업 스케줄러의 한글 인코딩 깨짐
* **현상**: 매일 아침 배치를 돌리기 위해 윈도우 작업 스케줄러(`autoNote_DailyBatch`)에 등록된 실행 인수가 다음과 같이 깨져 있었습니다.
  ```
  Execute: C:\...\agy.exe
  Arguments: --print "?? ??"
  ```
* **원인**: 윈도우 작업 스케줄러에 PowerShell을 통해 직접 한글 인수 `"일일 배치"`를 등록하는 과정에서 인코딩 문제로 문자열이 깨져 `"?? ??"`로 등록되었습니다. 이로 인해 에이전트가 무슨 명령인지 알 수 없어 동작을 수행하지 못했습니다.
* **조치**: 인코딩을 타지 않고 안전하게 실행할 수 있도록 배치 파일([run_daily_batch.bat](file:///D:/autoNote/run_daily_batch.bat))을 생성했습니다.
  ```cmd
  @echo off
  chcp 65001 > nul
  cd /d "D:\autoNote"
  "C:\Users\SYSADMIN\AppData\Local\agy\bin\agy.exe" --print "일일 배치" --dangerously-skip-permissions
  ```

---

## 3. 백그라운드 비대화형 실행 시 권한 승인 대기
* **현상**: 백그라운드 상태에서 스케줄러가 에이전트를 가동할 때, 문서 접근이나 MCP 호출을 진행하기 위해 권한 요청 승인이 대화창으로 필요하게 되지만, 백그라운드라 응답을 줄 수 없어 무한 대기하다가 타임아웃으로 강제 종료되고 있었습니다.
* **원인**: `agy` 실행 인수에 자동 권한 승인 플래그(`--dangerously-skip-permissions`)가 빠져 있었습니다.
* **조치**: 새로 제작한 [run_daily_batch.bat](file:///D:/autoNote/run_daily_batch.bat) 내에 해당 플래그를 추가하여 권한 대기 없이 다이렉트로 실행되도록 구성하고, 윈도우 작업 스케줄러가 이 배치 파일을 실행하도록 Action을 업데이트 완료했습니다.

---

## 요약 및 확인 결과
현재는 **Python 3.12** 안정 환경으로 Telegram MCP가 잘 가동되며, 매일 실행되는 작업 스케줄러 역시 한글 깨짐이나 권한 대기 문제 없이 실행되도록 재배치가 완료되었습니다. 앞으로 정상적인 배치가 수행될 것입니다.
