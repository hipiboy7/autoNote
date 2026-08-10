from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

class TelegramMessage(BaseModel):
    id: int
    channel_id: str
    text: str
    date: datetime
    sender_id: Optional[str] = None

class TelegramClient(ABC):
    @abstractmethod
    def list_messages(self, channel_id: str, limit: int = 100) -> List[TelegramMessage]:
        """특정 채널의 메시지 리스트를 최신순으로 가져온다."""
        pass

    def send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: Optional[str] = None,
    ) -> bool:
        """지정 대화에 메시지를 보낸다. 전송 성공 여부를 반환한다.

        추상 메서드가 아닌 기본 구현을 둔다. 수집 전용 구현체(테스트 목 등)가
        전송을 지원하지 않아도 되도록 하되, 조용히 성공한 척하지 않도록 False 를 준다.
        """
        return False
