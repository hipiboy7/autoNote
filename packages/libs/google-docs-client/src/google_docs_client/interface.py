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
        """지정된 문서의 전체 내용을 전달된 텍스트로 덮어쓴다.

        주의: Google Docs replaceAllText 는 **한 문단(paragraph) 내부**에서만 일치하므로
        여러 줄에 걸친 본문 전체를 한 번에 치환할 수 없다. 정밀 치환에는
        replace_line() 을 사용한다.
        """
        pass

    @abstractmethod
    def replace_line(self, doc_id: str, find_text: str, replace_text: str) -> bool:
        """문서 내 한 줄(문단)을 정확히 찾아 치환한다.

        실제로 치환이 일어났을 때만 True 를 반환해야 한다.
        치환 대상을 찾지 못했으면 False 를 반환한다 (조용한 성공 금지).
        """
        pass

    @abstractmethod
    def create_doc(self, title: str, content: str = "", folder_id: str = "") -> str:
        """새 Google Docs 문서를 만들고 문서 ID를 반환한다.

        folder_id 가 주어지면 해당 Drive 폴더로 이동시킨다.
        생성에 실패하면 빈 문자열을 반환한다.
        """
        pass

    @abstractmethod
    def apply_sepia_theme(self, doc_id: str) -> bool:
        """문서의 배경색을 세피아 크림색(#FDF6E3)으로 변경한다."""
        pass

    @abstractmethod
    def batch_update(self, doc_id: str, requests: list) -> bool:
        """커스텀 batchUpdate API 요청 리스트를 전송하여 실행한다."""
        pass
