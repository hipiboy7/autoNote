from datetime import datetime

from notify import ALERT_HEADER, OK_HEADER, AlertContext, BatchNotifier


class FakeTelegram:
    def __init__(self, ok=True, raises=False):
        self.ok = ok
        self.raises = raises
        self.sent = []

    def send_message(self, chat_id, text, parse_mode=None):
        if self.raises:
            raise RuntimeError("telegram 다운")
        self.sent.append((chat_id, text))
        return self.ok


class FakeDocs:
    def __init__(self, ok=True, raises=False):
        self.ok = ok
        self.raises = raises
        self.appended = []

    def append_text(self, doc_id, text):
        if self.raises:
            raise RuntimeError("docs 다운")
        self.appended.append((doc_id, text))
        return self.ok


def notifier(tg=None, docs=None, **kwargs):
    return BatchNotifier(
        telegram_client=tg,
        docs_client=docs,
        thought_doc_id="thought_doc",
        **kwargs,
    )


CONTEXT = AlertContext(
    mode="batch",
    when=datetime(2026, 8, 6, 8, 50, 3),
    last_message_id=785,
    stopped_at_message_id=790,
    processed=3,
)


# ------------------------------------------------------------------ 전달 경로

def test_failure_goes_to_both_telegram_and_docs():
    tg, docs = FakeTelegram(), FakeDocs()

    assert notifier(tg, docs).notify_failure("분석 실패로 중단", "LLMAnalysisError: ...", CONTEXT)

    assert len(tg.sent) == 1
    assert len(docs.appended) == 1
    chat_id, body = tg.sent[0]
    assert chat_id == "me"          # 기본값: 본인 저장 메시지 (푸시)
    assert ALERT_HEADER in body
    assert "분석 실패로 중단" in body


def test_telegram_chat_id_is_configurable(monkeypatch):
    monkeypatch.setenv("NOTIFY_TELEGRAM_CHAT_ID", "-100999")
    tg = FakeTelegram()

    notifier(tg).notify_failure("실패")

    assert tg.sent[0][0] == "-100999"


def test_context_details_are_included():
    tg = FakeTelegram()
    notifier(tg).notify_failure("중단", context=CONTEXT)
    body = tg.sent[0][1]

    assert "모드: batch" in body
    assert "2026-08-06 08:50:03" in body
    assert "중단 지점: message 790" in body
    assert "마지막 처리 ID: 785" in body
    assert "이번 실행 처리: 3건" in body


def test_body_includes_actionable_next_step():
    """알림만 오고 무엇을 해야 할지 모르면 쓸모가 없다."""
    tg = FakeTelegram()
    notifier(tg).notify_failure("중단")
    body = tg.sent[0][1]

    assert "--mode status" in body
    assert "run_daily_batch.bat" in body


def test_long_detail_is_clipped():
    """거대한 프롬프트/트레이스백이 알림을 덮지 않아야 한다."""
    tg = FakeTelegram()
    notifier(tg).notify_failure("중단", detail="X" * 5000)
    body = tg.sent[0][1]

    assert len(body) < 1500
    assert "이하 생략" in body


# ------------------------------------------------------------ 장애 내성

def test_telegram_failure_still_delivers_via_docs():
    tg, docs = FakeTelegram(raises=True), FakeDocs()

    assert notifier(tg, docs).notify_failure("중단") is True
    assert len(docs.appended) == 1


def test_docs_failure_still_delivers_via_telegram():
    tg, docs = FakeTelegram(), FakeDocs(raises=True)

    assert notifier(tg, docs).notify_failure("중단") is True
    assert len(tg.sent) == 1


def test_all_paths_failing_returns_false_and_prints_body(capsys):
    """알림이 전부 실패해도 예외를 던지지 않고 본문을 터미널에 남긴다."""
    tg, docs = FakeTelegram(raises=True), FakeDocs(raises=True)

    assert notifier(tg, docs).notify_failure("중단됨") is False

    err = capsys.readouterr().err
    assert "모든 알림 경로가 실패" in err
    assert "중단됨" in err


def test_no_clients_configured_does_not_raise():
    assert notifier().notify_failure("중단") is False


def test_disabled_notifier_sends_nothing():
    tg = FakeTelegram()
    assert notifier(tg, enabled=False).notify_failure("중단") is False
    assert tg.sent == []


def test_sink_returning_false_is_not_counted_as_delivered():
    tg = FakeTelegram(ok=False)
    assert notifier(tg).notify_failure("중단") is False


# ------------------------------------------------------------------ 복구 알림

def test_recovery_notification():
    tg, docs = FakeTelegram(), FakeDocs()

    assert notifier(tg, docs).notify_recovery("21건 처리 완료", CONTEXT)

    assert OK_HEADER in tg.sent[0][1]
    assert "21건 처리 완료" in tg.sent[0][1]


def test_docs_banner_uses_block_format():
    docs = FakeDocs()
    notifier(docs=docs).notify_failure("중단")

    _doc_id, block = docs.appended[0]
    assert block.startswith("━━━━━━━━━━━━━━━━━━\n")
    assert ALERT_HEADER in block
