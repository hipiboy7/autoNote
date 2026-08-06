"""기록에 쓰는 날짜의 시간대 기준.

텔레그램 메시지의 `date` 는 UTC 다. 이를 그대로 `%Y-%m-%d` 로 찍으면 한국 시간
오전 0~9시에 올라온 글이 **전날로 기록된다** (KST = UTC+9).

실측 사례 (2026-08-06):
    r806  UTC 08-05 23:17  =  KST 08-06 08:17  →  문서에 '2026-08-05' 로 기록됨
    r811  UTC 08-05 23:51  =  KST 08-06 08:51  →  문서에 '2026-08-05' 로 기록됨
그래서 오늘 아침 글 6건이 어제자로 붙어, 문서가 어제까지만 있는 것처럼 보였다.

영향 범위는 날짜 라벨만이 아니다. 월별 원문보관 라우팅(9월 1일 오전 글이 8월 문서로
감)과 주간리뷰의 7일 창 경계도 같이 밀린다. 그래서 '표시·분류에 쓰는 날짜' 는 모두
이 모듈을 거치게 한다.

`state.json` 의 `last_run`/`last_batch_at` 같은 **타임스탬프는 UTC 를 유지한다.**
경과 시간 계산에 쓰이므로 시간대를 바꾸면 오히려 틀린다.
"""

import os
from datetime import datetime, timedelta, timezone

DEFAULT_OFFSET_HOURS = 9  # Asia/Seoul


def offset_hours() -> int:
    raw = os.getenv("TIMEZONE_OFFSET_HOURS")
    if not raw:
        return DEFAULT_OFFSET_HOURS
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_OFFSET_HOURS


def local_tz() -> timezone:
    return timezone(timedelta(hours=offset_hours()))


def to_local(dt: datetime) -> datetime:
    """UTC(또는 tz 있는) 시각을 현지 시각으로 변환한다.

    tzinfo 가 없으면 이미 현지 시각으로 간주한다 (datetime.now() 로 만든 값).
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(local_tz())


def local_now() -> datetime:
    """현지 시각 (tz 정보 포함)."""
    return datetime.now(local_tz())


def local_date_str(dt: datetime) -> str:
    """문서에 기록할 날짜 문자열 (YYYY-MM-DD, 현지 기준)."""
    return to_local(dt).strftime("%Y-%m-%d")


def local_month_key(dt: datetime) -> str:
    """월별 문서 라우팅 키 (YYYY-MM, 현지 기준)."""
    return to_local(dt).strftime("%Y-%m")
