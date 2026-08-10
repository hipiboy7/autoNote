from datetime import datetime, timedelta, timezone

from autonote.batch_health import (
    VERDICT_FAILED,
    VERDICT_OK,
    VERDICT_STALE,
    VERDICT_UNKNOWN,
    evaluate_batch_health,
)
from autonote.notify import BatchNotifier
from autonote.watchdog import run_watchdog

NOW = datetime(2026, 8, 6, 9, 40, tzinfo=timezone.utc)


class FakeState:
    def __init__(self, **kwargs):
        self.last_message_id = kwargs.get("last_message_id", 785)
        self.last_batch_result = kwargs.get("last_batch_result")
        self.last_batch_at = kwargs.get("last_batch_at")
        self.last_batch_error = kwargs.get("last_batch_error", "")
        self.last_alert_date = kwargs.get("last_alert_date")


class FakeStateManager:
    def __init__(self, state):
        self.state = state
        self.alert_dates = []

    def load_state(self):
        return self.state

    def record_alert_date(self, date_str):
        self.state.last_alert_date = date_str
        self.alert_dates.append(date_str)


class RecordingTelegram:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append(text)
        return True


# ------------------------------------------------------------- 판정 로직

def test_ok_when_recent_success():
    state = FakeState(last_batch_result="ok", last_batch_at=NOW - timedelta(hours=1))
    verdict, reasons = evaluate_batch_health(state, NOW)

    assert verdict == VERDICT_OK
    assert "1시간 전" in reasons[0]


def test_failed_when_last_result_is_fail():
    """배치가 돌았지만 실패한 경우 (유형 A)."""
    state = FakeState(
        last_batch_result="fail",
        last_batch_at=NOW - timedelta(minutes=30),
        last_batch_error="LLMAnalysisError: 근거 검증 실패",
    )
    verdict, reasons = evaluate_batch_health(state, NOW)

    assert verdict == VERDICT_FAILED
    assert any("근거 검증 실패" in r for r in reasons)


def test_stale_when_batch_never_ran_today():
    """배치가 아예 돌지 않은 경우 (유형 B) — 배치 내부 알림으로는 못 잡는다."""
    state = FakeState(last_batch_result="ok", last_batch_at=NOW - timedelta(hours=30))
    verdict, reasons = evaluate_batch_health(state, NOW)

    assert verdict == VERDICT_STALE
    assert any("30시간 전" in r for r in reasons)
    assert any("스케줄러" in r for r in reasons)


def test_stale_threshold_is_configurable(monkeypatch):
    monkeypatch.setenv("BATCH_MAX_AGE_HOURS", "48")
    state = FakeState(last_batch_result="ok", last_batch_at=NOW - timedelta(hours=30))

    assert evaluate_batch_health(state, NOW)[0] == VERDICT_OK


def test_unknown_when_no_record():
    verdict, reasons = evaluate_batch_health(FakeState(), NOW)
    assert verdict == VERDICT_UNKNOWN
    assert "기록된 적이 없습니다" in reasons[0]


def test_naive_datetime_is_treated_as_utc():
    state = FakeState(last_batch_result="ok", last_batch_at=datetime(2026, 8, 6, 8, 50))
    assert evaluate_batch_health(state, NOW)[0] == VERDICT_OK


# ------------------------------------------------------------- 워치독

def test_watchdog_silent_when_healthy():
    tg = RecordingTelegram()
    sm = FakeStateManager(FakeState(last_batch_result="ok",
                                   last_batch_at=NOW - timedelta(hours=1)))

    rc = run_watchdog(sm, BatchNotifier(telegram_client=tg), now=NOW)

    assert rc == 0
    assert tg.sent == []


def test_watchdog_alerts_when_stale():
    tg = RecordingTelegram()
    sm = FakeStateManager(FakeState(last_batch_result="ok",
                                    last_batch_at=NOW - timedelta(hours=30)))

    rc = run_watchdog(sm, BatchNotifier(telegram_client=tg), now=NOW)

    assert rc == 1
    assert len(tg.sent) == 1
    assert "예정대로 실행되지 않았습니다" in tg.sent[0]
    assert "스케줄러와 VM 로그인" in tg.sent[0]
    assert sm.alert_dates == ["2026-08-06"]


def test_watchdog_does_not_repeat_same_day():
    """같은 이상을 하루에 여러 번 통보해 피곤하게 만들지 않는다."""
    tg = RecordingTelegram()
    sm = FakeStateManager(FakeState(
        last_batch_result="fail",
        last_batch_at=NOW - timedelta(hours=1),
        last_alert_date="2026-08-06",
    ))

    rc = run_watchdog(sm, BatchNotifier(telegram_client=tg), now=NOW)

    assert rc == 2
    assert tg.sent == []


def test_watchdog_does_not_record_suppression_when_delivery_fails():
    """전달 실패인데 억제 날짜를 기록하면 그날은 다시 알릴 기회가 사라진다."""
    class DeadTelegram:
        def send_message(self, chat_id, text, parse_mode=None):
            return False

    sm = FakeStateManager(FakeState(last_batch_result="fail",
                                    last_batch_at=NOW - timedelta(hours=1)))

    rc = run_watchdog(sm, BatchNotifier(telegram_client=DeadTelegram()), now=NOW)

    assert rc == 1
    assert sm.alert_dates == []
    assert sm.state.last_alert_date is None


def test_watchdog_alerts_again_on_a_new_day():
    tg = RecordingTelegram()
    sm = FakeStateManager(FakeState(
        last_batch_result="fail",
        last_batch_at=NOW - timedelta(hours=1),
        last_alert_date="2026-08-05",
    ))

    assert run_watchdog(sm, BatchNotifier(telegram_client=tg), now=NOW) == 1
    assert len(tg.sent) == 1
