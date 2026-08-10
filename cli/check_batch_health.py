"""배치가 예정대로 돌았는지 점검하고 이상하면 알린다 (워치독 진입점).

배치와 **독립적으로** 스케줄에 걸어야 의미가 있다. 배치가 시작조차 못한 경우
(스케줄러 고장, VM 꺼짐/로그아웃, batch.lock 잔존)는 배치 내부 알림으로 잡을 수 없다.

등록 예 (일일 배치 08:50 이후 여유를 두고 09:40):
    schtasks /create /tn "autoNote_HealthCheck" /tr "D:\\autoNote\\scripts\\run_health_check.bat" ^
             /sc daily /st 09:40 /f

종료 코드: 0 정상 / 1 이상 알림 발송 / 2 이상이지만 오늘 이미 알림 / 3 실행 오류
"""

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def main() -> int:
    from collector.service import StateManager
    from google_docs_client.mcp_client import MCPGoogleDocsClient
    from autonote.notify import BatchNotifier
    from telegram_client.mcp_client import MCPTelegramClient
    from autonote.watchdog import run_watchdog

    state_file = os.getenv("STATE_FILE_PATH", "./var/state.json")
    thought_doc_id = os.getenv(
        "THOUGHT_DOC_ID", "1p0d-bEHdVTzQjRnmXzzzB5HRvUCQSUyktUpksV70TUc"
    )

    state_manager = StateManager(state_file)
    notifier = BatchNotifier(
        telegram_client=MCPTelegramClient(),
        docs_client=MCPGoogleDocsClient(),
        thought_doc_id=thought_doc_id,
        enabled=os.getenv("NOTIFY_ENABLED", "1") not in ("0", "false", "False"),
    )

    return run_watchdog(state_manager, notifier)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 - 워치독이 조용히 죽으면 감시 의미가 없다
        print(f"[Watchdog] 점검 자체가 실패했습니다: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(3)
