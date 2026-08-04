from abc import ABC, abstractmethod
from typing import Type, TypeVar

T = TypeVar('T')

class LLMGateway(ABC):
    @abstractmethod
    def request_chat(
        self, 
        base_url: str, 
        api_key: str, 
        model: str, 
        messages: list, 
        temperature: float = 0.2
    ) -> str:
        """일반 텍스트 기반 completions API를 요청한다."""
        pass

    @abstractmethod
    def request_structured_chat(
        self, 
        base_url: str, 
        api_key: str, 
        model: str, 
        messages: list, 
        response_format: Type[T], 
        temperature: float = 0.2
    ) -> T:
        """Structured Output 규격을 준수하여 Pydantic 모델 형태의 데이터를 받아온다."""
        pass
