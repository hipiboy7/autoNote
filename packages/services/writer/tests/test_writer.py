import pytest
from datetime import datetime, timezone
from google_docs_client import GoogleDocsClient
from telegram_client import TelegramMessage
from analyzer import ResearchReport
from writer.service import ResearchWriter

class MockGoogleDocsClient(GoogleDocsClient):
    def __init__(self):
        self.write_log = {}
        self.sepia_applied = []

    def append_text(self, doc_id: str, text: str) -> bool:
        if doc_id not in self.write_log:
            self.write_log[doc_id] = []
        self.write_log[doc_id].append(text)
        return True

    def create_doc(self, title: str, content: str = "", folder_id: str = "") -> str:
        return f"created::{title}"

    def apply_sepia_theme(self, doc_id: str) -> bool:
        self.sepia_applied.append(doc_id)
        return True

    def read_text(self, doc_id: str) -> str:
        return ""

    def overwrite_text(self, doc_id: str, text: str) -> bool:
        return True

    def replace_line(self, doc_id: str, find_text: str, replace_text: str) -> bool:
        return True

    def batch_update(self, doc_id: str, requests: list) -> bool:
        # Mock batchUpdate
        if doc_id not in self.write_log:
            self.write_log[doc_id] = []
        self.write_log[doc_id].append(f"batchUpdate:{len(requests)} requests")
        return True

@pytest.fixture
def mock_client():
    return MockGoogleDocsClient()

@pytest.fixture
def sample_message():
    return TelegramMessage(
        id=305,
        channel_id="-1004421517798",
        text="반도체 HBM 수요가 견조하여 삼전의 실적 턴어라운드가 예상됩니다.\n세부 분석\n근거와 리스크 점검",
        date=datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
    )

@pytest.fixture
def sample_report():
    return ResearchReport(
        main_claim="삼성전자 반도체 부문의 HBM 공급 정상화로 실적 턴어라운드가 강력하게 나타날 것이다.",
        evidence=["HBM3E 엔비디아 퀄 테스트 통과 임박 수치 제시", "디램 현물가 상승 지표"],
        logic="퀄 테스트 완료 시 대규모 공급 계약 체결을 통한 마진 증가",
        risks=["퀄 승인 지연 리스크", "중국산 HBM 유입에 따른 단가 압박"],
        critical_question="엔비디아 외에 AMD 등으로 공급 다변화 로드맵이 존재하는가?",
        theme="반도체사이클"
    )

def test_writer_writes_original_backup(mock_client, sample_message):
    writer = ResearchWriter(mock_client)
    res = writer.write_original_backup("doc_orig_123", sample_message, title="삼전 HBM 실적 턴어라운드 예상")

    assert res is True
    written = mock_client.write_log["doc_orig_123"][0]
    assert "[r305]" in written
    assert "삼전 HBM 실적 턴어라운드 예상" in written
    assert "피터케이 채널" in written
    assert "반도체 HBM 수요가" in written
    assert "2026-07-15" in written

def test_writer_writes_research_report(mock_client, sample_message, sample_report):
    writer = ResearchWriter(mock_client)
    res = writer.write_research_report(
        doc_id="doc_theme_123",
        msg=sample_message,
        report=sample_report,
        prefix="t",
        title="삼성전자 HBM 실적 전망"
    )

    assert res is True
    written = mock_client.write_log["doc_theme_123"][0]
    assert "[t305]" in written
    assert "핵심 주장: 삼성전자 반도체 부문의" in written
    assert "근거·데이터:" in written
    assert "HBM3E 엔비디아" in written
    assert "판단 논리: 퀄 테스트" in written
    assert "💭 내 생각: 미기록" in written
    assert "❓ 생각해볼 질문: 엔비디아 외에" in written
    # 세피아 배경색 테마 일괄 적용 검증
    assert "doc_theme_123" in mock_client.sepia_applied

def test_writer_pre_fills_thought_note(mock_client, sample_message):
    writer = ResearchWriter(mock_client)
    res = writer.pre_fill_thought_note(
        doc_id="doc_thought_99",
        msg=sample_message,
        prefix="t",
        title="삼성전자 HBM 실적 전망"
    )

    assert res is True
    written = mock_client.write_log["doc_thought_99"][0]
    assert "2026-07-15 리서치 생각노트 작성 창구" in written
    assert "- [t305]" in written
    assert "삼성전자 HBM 실적 전망" in written
    assert "▶ 💭 내 생각:" in written
