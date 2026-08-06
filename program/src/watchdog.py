"""배치가 예정대로 돌았는지 점검하고 이상하면 알린다.

배치 **내부** 알림으로는 '배치가 아예 시작되지 않은 경우'(스케줄러 고장, VM 꺼짐/
로그아웃, 잠금 파일 잔존)를 잡을 수 없다. 그래서 배치와 독립적으로 스케줄에 걸어
state.json 을 들여다보는 감시자를 둔다.

같은 이상을 매 실행마다 반복 통보하지 않도록, 하루에 한 번만 알린다
(state.json 의 last_alert_date 로 억제).
"""

import sys
from datetime import datetime, timezone
from typing import Optional

from batch_health import VERDICT_OK, evaluate_batch_health
from notify import AlertContext, BatchNotifier


def run_watchdog(
    state_manager,
    notifier: BatchNotifier,
    now: Optional[datetime] = None,
    suppress_repeat: bool = True,
) -> int:
    """정상이면 0, 이상을 알렸으면 1, 이상이지만 이미 알린 상태면 2 를 반환한다."""
    now = now or datetime.now(timezone.utc)
    state = state_manager.load_state()
    verdict, reasons = evaluate_batch_health(state, now)

    if verdict == VERDICT_OK:
        print(f"[Watchdog] 정상: {' '.join(reasons)}")
        return 0

    today = now.strftime("%Y-%m-%d")
    already = getattr(state, "last_alert_date", None)
    if suppress_repeat and already == today:
        print(
            f"[Watchdog] {verdict} — 오늘({today}) 이미 알렸으므로 재통보하지 않습니다.",
            file=sys.stderr,
        )
        return 2

    delivered = notifier.notify_failure(
        summary=verdict,
        detail="\n".join(reasons),
        context=AlertContext(
            mode="watchdog",
            when=now,
            last_message_id=getattr(state, "last_message_id", None),
            hint="배치가 시작조차 못한 경우일 수 있습니다. 스케줄러와 VM 로그인을 먼저 확인하십시오.",
        ),
    )

    if not delivered:
        # 전달되지 않았는데 억제 날짜를 기록하면 그날은 다시 알릴 기회가 사라진다
        print(
            f"[Watchdog] {verdict} — 알림 전달에 실패했습니다. 억제 날짜를 기록하지 않습니다.",
            file=sys.stderr,
        )
        return 1

    state_manager.record_alert_date(today)
    print(f"[Watchdog] {verdict} — 알림을 발송했습니다.", file=sys.stderr)
    return 1
