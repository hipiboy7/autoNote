import os
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from telegram_client import TelegramClient, TelegramMessage

class ChannelState(BaseModel):
    last_message_id: int
    last_run: datetime

class State(BaseModel):
    last_message_id: int
    last_run: datetime
    channels: Dict[str, ChannelState] = Field(default_factory=dict)
    last_weekly_review_week: Optional[str] = None
    # 자동 생성된 월별 원문보관 문서 매핑 (YYYY-MM -> Google Docs ID)
    original_docs: Dict[str, str] = Field(default_factory=dict)

class StateManager:
    def __init__(self, file_path: str):
        self.file_path = file_path

    def load_state(self) -> State:
        # 파일이 없을 경우 초기 상태(기본값) 반환 및 자동 저장
        if not os.path.exists(self.file_path):
            state = State(
                last_message_id=0,
                last_run=datetime.now(timezone.utc),
                channels={}
            )
            self.save_state(state)
            return state

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return State.model_validate(data)
        except Exception as e:
            print(f"Error loading state.json: {e}. Backing up and creating new default state.")
            # JSON 손상 시 백업 생성 후 리셋
            backup_path = self.file_path + ".bak"
            if os.path.exists(self.file_path):
                os.replace(self.file_path, backup_path)
            state = State(
                last_message_id=0,
                last_run=datetime.now(timezone.utc),
                channels={}
            )
            self.save_state(state)
            return state

    def save_state(self, state: State) -> None:
        try:
            # 상위 폴더 자동 생성
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
            # 원자적 쓰기: temp 파일에 먼저 쓰고 os.replace로 교체 (중단 시 파일 손상 방지)
            tmp_path = self.file_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state.model_dump(mode="json"), f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.file_path)
        except Exception as e:
            print(f"Error saving state.json: {e}")

    def update_channel_state(self, channel_id: str, last_message_id: int) -> None:
        state = self.load_state()
        
        # 전역 상태 갱신
        state.last_message_id = max(state.last_message_id, last_message_id)
        state.last_run = datetime.now(timezone.utc)
        
        # 채널별 상태 갱신
        state.channels[channel_id] = ChannelState(
            last_message_id=last_message_id,
            last_run=datetime.now(timezone.utc)
        )
        
        self.save_state(state)

    def update_weekly_review_week(self, week_str: str) -> None:
        state = self.load_state()
        state.last_weekly_review_week = week_str
        self.save_state(state)

    def register_original_doc(self, month: str, doc_id: str) -> None:
        """자동 생성한 월별 원문보관 문서를 상태 파일에 등록한다."""
        state = self.load_state()
        state.original_docs[month] = doc_id
        self.save_state(state)

class MessageCollector:
    def __init__(self, client: TelegramClient, state_manager: StateManager):
        self.client = client
        self.state_manager = state_manager

    def collect_new_messages(self, channel_id: str) -> List[TelegramMessage]:
        state = self.state_manager.load_state()
        
        # 특정 채널에 대한 이전 수집 기록이 있으면 가져오고, 없으면 전역 기본값 사용
        if channel_id in state.channels:
            target_last_id = state.channels[channel_id].last_message_id
        else:
            target_last_id = state.last_message_id

        # 1. 텔레그램 채널에서 메시지 리스트 획득
        raw_messages = self.client.list_messages(channel_id, limit=200)

        # 2. 미수집 신규 메시지 수집 (필터링 없이 100% 수집)
        new_messages = [msg for msg in raw_messages if msg.id > target_last_id]

        # 3. 날짜순(과거 메시지가 앞으로 오도록 오름차순) 정렬
        new_messages.sort(key=lambda x: x.id)

        print(f"Collection statistics for {channel_id}: Collected={len(new_messages)} new messages.")
        return new_messages

    def collect_message_range(self, channel_id: str, from_id: int, to_id: int) -> List[TelegramMessage]:
        """특정 메시지 ID 범위의 메시지를 수집한다 (백필용). state.json을 참조하지 않는다."""
        raw_messages = self.client.list_messages(channel_id, limit=500)
        range_messages = [msg for msg in raw_messages if from_id <= msg.id <= to_id]
        range_messages.sort(key=lambda x: x.id)
        print(f"Backfill collection for {channel_id}: Collected={len(range_messages)} messages in range [{from_id}, {to_id}].")
        return range_messages

    def is_research(self, msg: TelegramMessage) -> bool:
        # URL 링크를 정규식으로 제거한 후 실제 텍스트 줄 수 계산
        import re
        text_without_urls = re.sub(r"https?://[^\s]+", "", msg.text)
        lines = [line.strip() for line in text_without_urls.split("\n") if line.strip()]
        
        # 실제 코멘트/분석 텍스트가 3줄 이하면 제외 (단순 링크글 및 짧은 잡담글)
        if len(lines) <= 3:
            return False

        # 광고, 공지사항, 투표 및 설문 키워드 필터링
        filter_keywords = [
            "광고", "제휴", "공구", "쿠팡 파트너스",
            "공지사항", "[공지]", "★공지", "투표", "설문조사"
        ]
        if any(keyword in msg.text for keyword in filter_keywords):
            return False

        return True
