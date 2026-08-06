from datetime import datetime, timedelta, timezone

from writer.timebase import (
    DEFAULT_OFFSET_HOURS,
    local_date_str,
    local_month_key,
    local_now,
    offset_hours,
    to_local,
)

UTC = timezone.utc


def test_default_offset_is_kst():
    assert DEFAULT_OFFSET_HOURS == 9
    assert offset_hours() == 9


def test_offset_is_configurable(monkeypatch):
    """하드코딩 금지 룰 — 시간대도 환경변수로 주입한다."""
    monkeypatch.setenv("TIMEZONE_OFFSET_HOURS", "0")
    assert offset_hours() == 0


def test_malformed_offset_falls_back(monkeypatch):
    monkeypatch.setenv("TIMEZONE_OFFSET_HOURS", "아홉")
    assert offset_hours() == DEFAULT_OFFSET_HOURS


# ------------------------------------------------- 실측 사례 회귀 테스트

def test_early_morning_kst_message_is_not_labelled_as_yesterday():
    """UTC 를 그대로 찍으면 한국 오전 글이 전날로 기록된다.

    실측 (2026-08-06): r811 은 UTC 08-05 23:51 = KST 08-06 08:51 인데
    문서에 '2026-08-05' 로 기록되어, 오늘 글이 어제자로 붙었다.
    """
    msg_utc = datetime(2026, 8, 5, 23, 51, tzinfo=UTC)

    assert msg_utc.strftime("%Y-%m-%d") == "2026-08-05"   # 과거 동작
    assert local_date_str(msg_utc) == "2026-08-06"        # 고친 동작


def test_late_evening_kst_message_keeps_same_day():
    msg_utc = datetime(2026, 8, 5, 12, 8, tzinfo=UTC)     # KST 08-05 21:08
    assert local_date_str(msg_utc) == "2026-08-05"


def test_month_routing_uses_local_month():
    """9월 1일 오전(KST) 글이 8월 문서로 가면 안 된다."""
    msg_utc = datetime(2026, 8, 31, 16, 30, tzinfo=UTC)   # KST 09-01 01:30

    assert msg_utc.strftime("%Y-%m") == "2026-08"          # 과거 동작
    assert local_month_key(msg_utc) == "2026-09"           # 고친 동작


def test_month_routing_keeps_month_when_not_crossing():
    msg_utc = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)    # KST 08-31 19:00
    assert local_month_key(msg_utc) == "2026-08"


# ------------------------------------------------------------- 변환 규칙

def test_naive_datetime_is_treated_as_already_local():
    """datetime.now() 로 만든 naive 값은 이미 현지 시각이므로 건드리지 않는다."""
    naive = datetime(2026, 8, 6, 8, 51)
    assert to_local(naive) == naive
    assert local_date_str(naive) == "2026-08-06"


def test_to_local_shifts_by_offset():
    msg_utc = datetime(2026, 8, 6, 0, 0, tzinfo=UTC)
    assert to_local(msg_utc).hour == 9


def test_local_now_carries_offset():
    now = local_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(hours=9)
