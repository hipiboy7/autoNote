from abc import ABC, abstractmethod

class GoogleDocsClient(ABC):
    @abstractmethod
    def append_text(self, doc_id: str, text: str) -> bool:
        """문서의 맨 끝에 지정된 텍스트를 추가한다."""
        pass

    @abstractmethod
    def read_text(self, doc_id: str) -> str:
        """지정된 문서의 전체 텍스트 내용을 읽어온다."""
        pass

    @abstractmethod
    def overwrite_text(self, doc_id: str, text: str) -> bool:
        """지정된 문서의 전체 내용을 전달된 텍스트로 덮어쓴다."""
        pass

    @abstractmethod
    def apply_sepia_theme(self, doc_id: str) -> bool:
        """문서의 배경색을 세피아 크림색(#FDF6E3)으로 변경한다."""
        pass

    @abstractmethod
    def batch_update(self, doc_id: str, requests: list) -> bool:
        """커스텀 batchUpdate API 요청 리스트를 전송하여 실행한다."""
        pass
