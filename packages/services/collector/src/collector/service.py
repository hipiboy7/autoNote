import os
import sys
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from telegram_client import TelegramClient, TelegramMessage

from .triage import ResearchTriage

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
    # 문서 복구로 재생성된 문서의 매핑 교체 (테마명 -> 새 Google Docs ID)
    doc_overrides: Dict[str, str] = Field(default_factory=dict)
    # 마지막 배치 실행 결과 — 워치독이 '배치가 아예 안 돌았는지' 판정하는 근거
    last_batch_result: Optional[str] = None      # "ok" | "fail"
    last_batch_at: Optional[datetime] = None
    last_batch_error: Optional[str] = None
    # 워치독이 같은 이상을 하루에 한 번만 통보하도록 하는 억제 키 (YYYY-MM-DD)
    last_alert_date: Optional[str] = None

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

    def register_doc_override(self, key: str, doc_id: str) -> None:
        """문서 복구로 재생성된 문서를 매핑 교체 대상으로 등록한다."""
        state = self.load_state()
        state.doc_overrides[key] = doc_id
        self.save_state(state)

    def remove_doc_override(self, key: str) -> None:
        """되쓰기가 끝나 원본이 현행이 되면 우회 매핑을 제거한다."""
        state = self.load_state()
        if state.doc_overrides.pop(key, None) is not None:
            self.save_state(state)

    def record_alert_date(self, date_str: str) -> None:
        """워치독이 알림을 보낸 날짜를 기록해 중복 통보를 막는다."""
        state = self.load_state()
        state.last_alert_date = date_str
        self.save_state(state)

    def record_batch_result(self, ok: bool, error: str = "") -> Optional[str]:
        """배치 실행 결과를 기록하고 **직전** 결과를 반환한다.

        직전 결과를 돌려주는 이유: 실패 → 성공으로 바뀐 시점에만 복구 알림을 보내
        매일 성공 알림으로 사용자를 피곤하게 하지 않기 위함이다.
        """
        state = self.load_state()
        previous = state.last_batch_result
        state.last_batch_result = "ok" if ok else "fail"
        state.last_batch_at = datetime.now(timezone.utc)
        state.last_batch_error = "" if ok else (error or "")[:500]
        if ok:
            # 정상 복귀했으면 억제를 풀어, 다음에 다시 이상이 생기면 알림이 나가게 한다
            state.last_alert_date = None
        self.save_state(state)
        return previous

# 한 번에 가져오는 최신 글 수와 늘려 가며 다시 가져올 때의 상한.
# 하루 15건 안팎이라 3200건이면 반년치다. 넘으면 TELEGRAM_FETCH_MAX_LIMIT 로 올린다.
FETCH_INITIAL_LIMIT = 200
FETCH_MAX_LIMIT = 3200


def fetch_max_limit() -> int:
    try:
        return max(FETCH_INITIAL_LIMIT, int(os.getenv("TELEGRAM_FETCH_MAX_LIMIT", FETCH_MAX_LIMIT)))
    except ValueError:
        return FETCH_MAX_LIMIT


class CollectionGapError(RuntimeError):
    """마지막 처리 ID 까지 거슬러 올라가지 못해 중간 글을 놓칠 수 있을 때."""


class MessageCollector:
    def __init__(
        self,
        client: TelegramClient,
        state_manager: StateManager,
        initial_limit: int = FETCH_INITIAL_LIMIT,
        max_limit: Optional[int] = None,
    ):
        self.client = client
        self.state_manager = state_manager
        self.initial_limit = initial_limit
        self.max_limit = max_limit if max_limit is not None else fetch_max_limit()

    def _fetch_back_to(self, channel_id: str, boundary_id: int, initial_limit: int) -> List[TelegramMessage]:
        """boundary_id 보다 큰 글이 **빠짐없이** 들어올 때까지 limit 을 늘려 가져온다.

        list_messages 는 최신 글부터 limit 건만 준다 (MCP 도구에 ID 기준 페이지 인자가 없다).
        예전에는 limit=200 한 번으로 끝내, 밀린 글이 200건을 넘으면 그보다 오래된 글을
        조용히 건너뛰고 state 가 최신 ID 로 넘어가 영영 수집되지 않았다.

        다 가져왔다고 보는 경우는 둘이다.
          * 결과에 boundary_id 이하인 글이 있다 → 경계까지 겹쳐 읽었다
          * 결과가 limit 보다 적다            → 채널 끝까지 읽었다
        상한까지 늘려도 어느 쪽도 아니면 CollectionGapError 로 멈춘다 (배치 실패 알림으로 간다).
        """
        limit = initial_limit
        while True:
            raw = self.client.list_messages(channel_id, limit=limit)
            if len(raw) < limit or any(m.id <= boundary_id for m in raw):
                return raw
            if limit >= self.max_limit:
                oldest = min(m.id for m in raw)
                raise CollectionGapError(
                    f"최신 {len(raw)}건을 가져와도 마지막 처리 ID {boundary_id} 에 닿지 않습니다 "
                    f"(가장 오래된 수집 ID {oldest}). 그대로 진행하면 {boundary_id + 1}~{oldest - 1} "
                    "구간을 놓칩니다. 백필도 같은 최신 N건 조회를 쓰므로 메울 수 없습니다 — "
                    f"TELEGRAM_FETCH_MAX_LIMIT 를 {self.max_limit} 보다 크게 지정해 다시 실행하십시오."
                )
            limit = min(limit * 2, self.max_limit)
            print(
                f"[Collector] 마지막 처리 ID {boundary_id} 까지 닿지 않아 limit={limit} 로 다시 가져옵니다.",
                file=sys.stderr,
            )

    def collect_new_messages(self, channel_id: str) -> List[TelegramMessage]:
        state = self.state_manager.load_state()
        
        # 특정 채널에 대한 이전 수집 기록이 있으면 가져오고, 없으면 전역 기본값 사용
        if channel_id in state.channels:
            target_last_id = state.channels[channel_id].last_message_id
        else:
            target_last_id = state.last_message_id

        # 1. 텔레그램 채널에서 마지막 처리 ID 이후 글을 빠짐없이 획득.
        #    state 가 없던 첫 실행(0)은 경계가 없으므로 최신 글만 가져온다 (기존 동작).
        if target_last_id > 0:
            raw_messages = self._fetch_back_to(channel_id, target_last_id, self.initial_limit)
        else:
            raw_messages = self.client.list_messages(channel_id, limit=self.initial_limit)

        # 2. 미수집 신규 메시지 수집 (필터링 없이 100% 수집)
        new_messages = [msg for msg in raw_messages if msg.id > target_last_id]

        # 3. 날짜순(과거 메시지가 앞으로 오도록 오름차순) 정렬
        new_messages.sort(key=lambda x: x.id)

        print(f"Collection statistics for {channel_id}: Collected={len(new_messages)} new messages.")
        return new_messages

    def collect_message_range(self, channel_id: str, from_id: int, to_id: int) -> List[TelegramMessage]:
        """특정 메시지 ID 범위의 메시지를 수집한다 (백필용). state.json을 참조하지 않는다."""
        raw_messages = self._fetch_back_to(channel_id, from_id - 1, 500)
        range_messages = [msg for msg in raw_messages if from_id <= msg.id <= to_id]
        range_messages.sort(key=lambda x: x.id)
        print(f"Backfill collection for {channel_id}: Collected={len(range_messages)} messages in range [{from_id}, {to_id}].")
        return range_messages

    def obvious_skip_reason(self, msg: TelegramMessage) -> Optional[str]:
        """프로그램이 확정할 수 있는 비리서치 사유만 반환한다 (토큰 0).

        줄 수 기반 판정은 더 이상 쓰지 않는다. 실측 결과 최근 200건 중 118건(59%)이
        기존 '3줄 이하 제외' 규칙에 탈락했고, 그중에는 사이드카·수급 판단처럼 짧지만
        핵심적인 코멘트가 있었다. 애매한 글은 ResearchTriage 워커가 배치로 판별한다.
        """
        return ResearchTriage.obvious_skip_reason(msg)
