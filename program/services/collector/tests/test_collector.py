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

    # 검증 3: 개별 메시지에 대한 리서치 선별(is_research) 검증
    # - ID 101 (짧은 글): 리서치 아님
    assert not collector.is_research(collected[0]) 
    # - ID 102 (정상 반도체 리서치): 리서치 맞음
    assert collector.is_research(collected[1])
    # - ID 103 (광고/제휴): 리서치 아님
    assert not collector.is_research(collected[2])
    # - ID 104 (정상 환율 리서치): 리서치 맞음
    assert collector.is_research(collected[3])

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

def test_collector_filters_simple_links(temp_state_file):
    state_manager = StateManager(temp_state_file)
    collector = MessageCollector(None, state_manager)

    # 1. 단순 링크글 -> 리서치 제외
    msg1 = TelegramMessage(id=1, channel_id="c", text="https://blog.naver.com/some_post", date=datetime.now(timezone.utc))
    assert not collector.is_research(msg1)

    # 2. 짧은 코멘트 동반 링크글 -> 리서치 제외
    msg2 = TelegramMessage(id=2, channel_id="c", text="링크 공유합니다.\nhttps://easyconomics.com", date=datetime.now(timezone.utc))
    assert not collector.is_research(msg2)

    # 3. 풍부한 분석 동반 링크글 -> 리서치 포함
    msg3 = TelegramMessage(id=3, channel_id="c", text="삼성전자 ADR 분석\n라인 2 코멘트\n라인 3 데이터\n라인 4 결론\nhttps://m.blog.naver.com", date=datetime.now(timezone.utc))
    assert collector.is_research(msg3)

def test_collector_filters_announcements_and_polls(temp_state_file):
    state_manager = StateManager(temp_state_file)
    collector = MessageCollector(None, state_manager)

    # 1. 공지사항 -> 리서치 제외
    msg1 = TelegramMessage(id=1, channel_id="c", text="[공지] 다음주 일정 공지합니다.\n라인 2\n라인 3\n라인 4", date=datetime.now(timezone.utc))
    assert not collector.is_research(msg1)

    # 2. 투표 -> 리서치 제외
    msg2 = TelegramMessage(id=2, channel_id="c", text="설문조사 참여 부탁드립니다.\n1. 매수\n2. 매도\n3. 관망\n많은 참여 바랍니다.", date=datetime.now(timezone.utc))
    assert not collector.is_research(msg2)

    # 3. 정상 리서치 -> 리서치 포함
    msg3 = TelegramMessage(id=3, channel_id="c", text="삼성전자 공급망 분석\n라인 2 내용\n라인 3 데이터\n라인 4 결론", date=datetime.now(timezone.utc))
    assert collector.is_research(msg3)
