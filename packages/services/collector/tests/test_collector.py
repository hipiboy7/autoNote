import os
import json
from datetime import datetime, timezone
import pytest
from telegram_client import TelegramMessage, TelegramClient
from collector.service import MessageCollector, StateManager, State, ChannelState

class MockTelegramClient(TelegramClient):
    def __init__(self, messages: list[TelegramMessage]):
        self.messages = messages

    def list_messages(self, channel_id: str, limit: int = 100) -> list[TelegramMessage]:
        return [msg for msg in self.messages if msg.channel_id == channel_id]

@pytest.fixture
def temp_state_file(tmp_path):
    return os.path.join(tmp_path, "state.json")

def test_collector_collects_all_new_messages_and_filters_correctly(temp_state_file):
    # 1. 가짜 상태 파일 준비
    state_data = {
        "last_message_id": 100,
        "last_run": "2026-07-15T12:00:00Z",
        "channels": {
            "-1004421517798": {
                "last_message_id": 100,
                "last_run": "2026-07-15T12:00:00Z"
            }
        }
    }
    with open(temp_state_file, "w", encoding="utf-8") as f:
        json.dump(state_data, f)

    mock_messages = [
        # 이미 처리된 글 (ID <= 100) -> 수집되지 않아야 함
        TelegramMessage(id=99, channel_id="-1004421517798", text="이미 수집된 잡담", date=datetime.now(timezone.utc)),
        TelegramMessage(id=100, channel_id="-1004421517798", text="이미 수집된 리서치", date=datetime.now(timezone.utc)),
        
        # 신규 메시지 (ID > 100) -> 100% 전부 수집되어야 함 (원문 보관용)
        TelegramMessage(id=102, channel_id="-1004421517798", text="정상적인 반도체 리서치 보고서\n라인 2 내용\n라인 3 데이터\n라인 4 결론", date=datetime.now(timezone.utc)),
        TelegramMessage(id=101, channel_id="-1004421517798", text="짧은 한 줄 의견글\n두 줄째", date=datetime.now(timezone.utc)),
        TelegramMessage(id=103, channel_id="-1004421517798", text="추천 제휴 서비스 안내\n라인 2 내용\n라인 3 데이터\n라인 4 결론", date=datetime.now(timezone.utc)),
        TelegramMessage(id=104, channel_id="-1004421517798", text="환율 매크로 전망 분석\n라인 2 내용\n라인 3 데이터\n라인 4 결론", date=datetime.now(timezone.utc)),
    ]

    client = MockTelegramClient(mock_messages)
    state_manager = StateManager(temp_state_file)
    collector = MessageCollector(client, state_manager)

    # 수집 실행 (원문용 수집이므로 100% 가져옴)
    collected = collector.collect_new_messages("-1004421517798")

    # 검증 1: 신규 4개 메시지가 유실 없이 전부 수집되어야 함
    assert len(collected) == 4
    # 검증 2: 메시지 ID 오름차순 정렬이어야 함 (101, 102, 103, 104)
    assert [msg.id for msg in collected] == [101, 102, 103, 104]

    # 검증 3: 프로그램 사전 필터는 '확실한 것'만 걸러낸다 (토큰 0)
    # - ID 101 (짧은 글): 짧다는 이유로 버리지 않는다 → triage 워커가 판별
    assert collector.obvious_skip_reason(collected[0]) is None
    # - ID 102 (정상 반도체 리서치): 통과
    assert collector.obvious_skip_reason(collected[1]) is None
    # - ID 103 (광고/제휴): 프로그램이 확정 제외
    assert collector.obvious_skip_reason(collected[2]) == "광고·공지 키워드"
    # - ID 104 (정상 환율 리서치): 통과
    assert collector.obvious_skip_reason(collected[3]) is None

def test_collector_fallback_when_state_file_missing(temp_state_file):
    if os.path.exists(temp_state_file):
        os.remove(temp_state_file)

    mock_messages = [
        TelegramMessage(id=500, channel_id="-1004421517798", text="최근 리서치 코멘트\n라인 2\n라인 3\n라인 4", date=datetime.now(timezone.utc)),
    ]

    client = MockTelegramClient(mock_messages)
    state_manager = StateManager(temp_state_file)
    collector = MessageCollector(client, state_manager)

    collected = collector.collect_new_messages("-1004421517798")
    
    assert len(collected) == 1
    assert collected[0].id == 500
    assert os.path.exists(temp_state_file)

def test_state_manager_handles_corrupt_json(temp_state_file):
    with open(temp_state_file, "w", encoding="utf-8") as f:
        f.write("invalid json {")

    state_manager = StateManager(temp_state_file)
    state = state_manager.load_state()

    assert state.last_message_id == 0
    assert os.path.exists(temp_state_file + ".bak")

def test_state_manager_handles_save_error(tmp_path, capsys):
    invalid_path = os.path.join(tmp_path, "non_existent_folder", "invalid|file:name")
    state_manager = StateManager(invalid_path)
    state = state_manager.load_state()

    captured = capsys.readouterr()
    assert "Error saving state.json" in captured.out
def test_program_filter_only_rejects_certain_cases(temp_state_file):
    """프로그램 사전 필터는 확실한 것만 걸러낸다. 줄 수는 기준이 아니다."""
    state_manager = StateManager(temp_state_file)
    collector = MessageCollector(None, state_manager)

    def msg(text, msg_id=1):
        return TelegramMessage(id=msg_id, channel_id="c", text=text,
                               date=datetime.now(timezone.utc))

    # 1. 링크만 있는 글 -> 확정 제외
    assert collector.obvious_skip_reason(msg("https://blog.naver.com/some_post")) == "링크만 있음"

    # 2. 본문 없는 미디어 전용 글 -> 확정 제외
    assert collector.obvious_skip_reason(msg("[empty]")) == "본문 없음(미디어 전용)"
    assert collector.obvious_skip_reason(msg("   ")) == "본문 없음(미디어 전용)"

    # 3. 광고·공지·투표 -> 확정 제외
    assert collector.obvious_skip_reason(msg("[공지] 다음주 일정 공지합니다.")) == "광고·공지 키워드"
    assert collector.obvious_skip_reason(msg("설문조사 참여 부탁드립니다.")) == "광고·공지 키워드"
    assert collector.obvious_skip_reason(msg("추천 제휴 서비스 안내")) == "광고·공지 키워드"

    # 4. 짧은 판단 코멘트 -> 프로그램이 버리지 않는다 (triage 워커가 판별)
    #    과거 3줄 규칙이 이런 글을 탈락시켜 사람이 수동 보정해야 했다 (r753·r755).
    short_call = msg("코스닥 3일연속 사이드카.\n바닥에서는 단기 과열권인듯 합니다")
    assert collector.obvious_skip_reason(short_call) is None

    # 5. 코멘트 동반 링크글 -> 프로그램이 버리지 않는다
    with_comment = msg("전쟁 장기화가 쉽지 않은 상황.\nhttps://m.blog.naver.com/x")
    assert collector.obvious_skip_reason(with_comment) is None


class LimitedTelegramClient(TelegramClient):
    """실서버처럼 최신 글부터 limit 건만 돌려준다."""

    def __init__(self, ids):
        self.ids = sorted(ids, reverse=True)
        self.limits = []

    def list_messages(self, channel_id: str, limit: int = 100) -> list[TelegramMessage]:
        self.limits.append(limit)
        return [
            TelegramMessage(id=i, channel_id=channel_id, text=f"글 {i}", date=datetime.now(timezone.utc))
            for i in self.ids[:limit]
        ]


def _collector_after(temp_state_file, last_id, client, **kwargs):
    state_manager = StateManager(temp_state_file)
    state_manager.update_channel_state("c", last_id)
    return MessageCollector(client, state_manager, **kwargs)


def test_collector_refetches_until_backlog_reaches_last_id(temp_state_file):
    """밀린 글이 200건을 넘어도 오래된 글을 놓치지 않는다."""
    client = LimitedTelegramClient(range(1, 1001))  # 1~1000
    collector = _collector_after(temp_state_file, 500, client)

    collected = collector.collect_new_messages("c")

    assert [m.id for m in collected] == list(range(501, 1001))
    assert client.limits == [200, 400, 800]


def test_collector_single_fetch_when_backlog_is_small(temp_state_file):
    client = LimitedTelegramClient(range(1, 1001))
    collector = _collector_after(temp_state_file, 950, client)

    assert len(collector.collect_new_messages("c")) == 50
    assert client.limits == [200]


def test_collector_stops_when_channel_is_exhausted(temp_state_file):
    """채널 글이 limit 보다 적으면 더 늘리지 않는다 (경계 글이 지워진 경우 포함)."""
    client = LimitedTelegramClient(range(300, 450))
    collector = _collector_after(temp_state_file, 100, client)

    assert len(collector.collect_new_messages("c")) == 150
    assert client.limits == [200]


def test_collector_raises_instead_of_skipping_gap(temp_state_file):
    """상한까지 늘려도 닿지 않으면 state 를 넘기지 않도록 멈춘다."""
    from collector import CollectionGapError

    client = LimitedTelegramClient(range(1, 2001))
    collector = _collector_after(temp_state_file, 100, client, max_limit=800)

    with pytest.raises(CollectionGapError, match="101~1200"):
        collector.collect_new_messages("c")
    assert client.limits == [200, 400, 800]
    assert StateManager(temp_state_file).load_state().channels["c"].last_message_id == 100


def test_collect_message_range_reaches_old_from_id(temp_state_file):
    client = LimitedTelegramClient(range(1, 2001))
    collector = _collector_after(temp_state_file, 2000, client)

    got = collector.collect_message_range("c", 1000, 1010)

    assert [m.id for m in got] == list(range(1000, 1011))
    assert client.limits == [500, 1000, 2000]


def test_fetch_max_limit_env(monkeypatch):
    from collector.service import fetch_max_limit

    monkeypatch.setenv("TELEGRAM_FETCH_MAX_LIMIT", "")
    assert fetch_max_limit() == 3200
    monkeypatch.setenv("TELEGRAM_FETCH_MAX_LIMIT", "10000")
    assert fetch_max_limit() == 10000
