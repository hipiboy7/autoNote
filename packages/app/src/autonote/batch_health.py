"""배치가 정상적으로 돌았는지 판정한다.

`--mode status`(사람이 직접 확인)와 워치독(스케줄러가 자동 확인)이 같은 판정 로직을
쓰도록 분리했다. 판정 기준이 두 곳에서 갈라지면 "상태는 정상인데 알림이 온다" 같은
혼란이 생긴다.

잡아야 하는 두 가지 이상:
  A. 배치가 돌았지만 실패했다      → last_batch_result == "fail"
  B. 배치가 아예 돌지 않았다        → last_batch_at 이 기준 시각보다 오래됐다
     (스케줄러 고장, VM 꺼짐/로그아웃, 잠금 파일 잔존 등)
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any, List, Tuple

VERDICT_OK = "정상"
VERDICT_FAILED = "이상 — 마지막 배치가 실패했습니다"
VERDICT_STALE = "이상 — 배치가 예정대로 실행되지 않았습니다"
VERDICT_UNKNOWN = "확인 필요 — 배치 실행 기록이 없습니다"

# 마지막 성공으로부터 이 시간이 지나면 '안 돌았다'로 본다.
# 일일 배치(08:50) 기준으로 하루를 넘기면 이상이다.
DEFAULT_MAX_AGE_HOURS = 26


def max_age_hours() -> int:
    raw = os.getenv("BATCH_MAX_AGE_HOURS")
    if not raw:
        return DEFAULT_MAX_AGE_HOURS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_AGE_HOURS


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def evaluate_batch_health(
    state: Any,
    now: datetime,
    max_age: int = 0,
) -> Tuple[str, List[str]]:
    """(판정, 사유 목록) 을 반환한다. 판정이 VERDICT_OK 가 아니면 알림 대상이다."""
    limit_hours = max_age or max_age_hours()
    now_utc = _as_utc(now)
    reasons: List[str] = []

    last_result = getattr(state, "last_batch_result", None)
    last_at = getattr(state, "last_batch_at", None)

    if last_result is None and last_at is None:
        return VERDICT_UNKNOWN, [
            "배치 실행 결과가 기록된 적이 없습니다. 한 번 실행해 기준을 만드십시오.",
        ]

    if last_result == "fail":
        error = (getattr(state, "last_batch_error", "") or "").strip()
        reasons.append("마지막 배치가 실패로 끝났습니다.")
        if error:
            reasons.append(f"오류: {error[:200]}")
        return VERDICT_FAILED, reasons

    if last_at is None:
        return VERDICT_UNKNOWN, ["마지막 배치 시각이 기록되지 않았습니다."]

    age = now_utc - _as_utc(last_at)
    if age > timedelta(hours=limit_hours):
        hours = int(age.total_seconds() // 3600)
        reasons.append(
            f"마지막 성공 배치가 {hours}시간 전입니다 (허용 {limit_hours}시간)."
        )
        reasons.append(
            "스케줄러(autoNote_DailyBatch), VM 로그인 상태, var/batch.lock 잔존을 확인하십시오."
        )
        return VERDICT_STALE, reasons

    hours = int(age.total_seconds() // 3600)
    return VERDICT_OK, [f"마지막 성공 배치가 {hours}시간 전입니다."]
