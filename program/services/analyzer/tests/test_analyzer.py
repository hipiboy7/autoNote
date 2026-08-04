import pytest
from unittest.mock import MagicMock
from pydantic import ValidationError
from llm_gateway import LLMGateway
from analyzer.service import ResearchAnalyzer, ResearchReport, LLMAnalysisError
from analyzer.config import LLMSettings

class MockLLMGateway(LLMGateway):
    def __init__(self, response_obj=None, fail_count=0):
        self.response_obj = response_obj
        self.fail_count = fail_count
        self.calls = 0

    def request_chat(self, base_url: str, api_key: str, model: str, messages: list, temperature: float = 0.2) -> str:
        return "mock text response"

    def request_structured_chat(self, base_url: str, api_key: str, model: str, messages: list, response_format: type, temperature: float = 0.2):
        self.calls += 1
        if self.calls <= self.fail_count:
            # Pydantic validation error를 유발하기 위해 dict 구조가 깨진 데이터 리턴(또는 예외 발생)
            raise ValidationError.from_exception_data(
                "MockValidationError",
                line_errors=[{"type": "missing", "loc": ("main_claim",), "input": {}}]
            )
        return self.response_obj

@pytest.fixture
def mock_settings():
    return LLMSettings(
        chat_llm_base_url="https://api.openai.com/v1",
        chat_llm_model="gpt-4o",
        chat_llm_api_key="mock-key",
        reasoning_llm_base_url="https://api.openai.com/v1",
        reasoning_llm_model="o4-mini",
        reasoning_llm_api_key="mock-key"
    )

def test_analyzer_returns_structured_report(mock_settings):
    expected_report = ResearchReport(
        main_claim="삼성전자 ADR 상장은 지배구조 문제로 인해 제한적이다.",
        evidence=["SK하이닉스는 지배구조가 단순하나 삼성전자는 복잡하다", "ADR 상장 시 의결권 3% 제한 룰이 장애물이다"],
        logic="복잡한 순환출자 및 특별관계자 지분 제한 법령이 결합하여 해외 직접 ADR 상장 추진을 가로막음",
        risks=["ADR 불발 시 글로벌 유동성 수혜 지연", "주가 부진 장기화 리스크"],
        critical_question="순환출자 해소를 위한 지배구조 개편 로드맵이 존재하는가?",
        theme="반도체사이클"
    )

    gateway = MockLLMGateway(response_obj=expected_report)
    analyzer = ResearchAnalyzer(gateway, mock_settings)

    report = analyzer.analyze_message("삼성전자 ADR 관련 원문 텍스트...")

    assert report.main_claim == expected_report.main_claim
    assert len(report.evidence) == 2
    assert report.critical_question == expected_report.critical_question
    assert gateway.calls == 1

def test_analyzer_retries_on_validation_failure_and_succeeds(mock_settings):
    expected_report = ResearchReport(
        main_claim="성공적인 핵심 요약",
        evidence=["근거 1"],
        logic="추론 로직",
        risks=["리스크 1"],
        critical_question="질문 1",
        theme="시장심리·수급"
    )

    # 1회 실패 후 2회째 성공하는 시나리오
    gateway = MockLLMGateway(response_obj=expected_report, fail_count=1)
    analyzer = ResearchAnalyzer(gateway, mock_settings)

    report = analyzer.analyze_message("원문 텍스트...")

    assert report.main_claim == "성공적인 핵심 요약"
    assert gateway.calls == 2 # 1차 실패 ➔ 2차 성공

def test_analyzer_fails_after_max_retries(mock_settings):
    # 3회 연속 실패하는 시나리오 (최대 재시도 2회를 넘음)
    gateway = MockLLMGateway(response_obj=None, fail_count=3)
    analyzer = ResearchAnalyzer(gateway, mock_settings)

    with pytest.raises(LLMAnalysisError) as exc_info:
        analyzer.analyze_message("원문 텍스트...")

    assert "LLM analysis failed after max retries" in str(exc_info.value)
    assert gateway.calls == 3 # 1차 시도 ➔ 1차 재시도 ➔ 2차 재시도 ➔ 에러 전파
