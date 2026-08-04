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
