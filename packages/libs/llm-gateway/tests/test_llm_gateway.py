import pytest
from unittest.mock import patch, MagicMock
from pydantic import BaseModel
from llm_gateway.openai_gateway import LLMUnavailableError, OpenAILikeGateway

class TestSchema(BaseModel):
    key: str
    value: int

@patch("llm_gateway.openai_gateway.OpenAI")
def test_openai_gateway_request_chat(mock_openai_class):
    # 1. OpenAI 클라이언트 Mocking
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content="hello world"))
    ]
    mock_client.chat.completions.create.return_value = mock_response

    # 2. 실행
    gateway = OpenAILikeGateway()
    res = gateway.request_chat(
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}]
    )

    # 3. 검증
    assert res == "hello world"
    mock_openai_class.assert_called_once_with(base_url="https://api.openai.com/v1", api_key="sk-test")
    mock_client.chat.completions.create.assert_called_once_with(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.2
    )

@patch("llm_gateway.openai_gateway.OpenAI")
def test_openai_gateway_request_structured_chat(mock_openai_class):
    # 1. OpenAI 클라이언트 Mocking
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    
    mock_parsed_obj = TestSchema(key="test_key", value=42)
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(parsed=mock_parsed_obj))
    ]
    mock_client.beta.chat.completions.parse.return_value = mock_response

    # 2. 실행
    gateway = OpenAILikeGateway()
    res = gateway.request_structured_chat(
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        response_format=TestSchema
    )

    # 3. 검증
    assert res.key == "test_key"
    assert res.value == 42
    mock_client.beta.chat.completions.parse.assert_called_once_with(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        response_format=TestSchema,
        temperature=0.2
    )

@patch("llm_gateway.openai_gateway.OpenAI")
def test_openai_gateway_request_structured_chat_raises_value_error_on_null_parsed(mock_openai_class):
    # 1. OpenAI 클라이언트 Mocking (parsed=None 반환하도록 설정)
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(parsed=None))
    ]
    mock_client.beta.chat.completions.parse.return_value = mock_response

    # 2. 실행 및 검증
    gateway = OpenAILikeGateway()
    with pytest.raises(ValueError) as exc_info:
        gateway.request_structured_chat(
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4o",
            messages=[{"role": "user", "content": "hi"}],
            response_format=TestSchema
        )

    assert "LLM responded with null or could not be parsed" in str(exc_info.value)


# --- 창작 폴백 금지 회귀 테스트 ------------------------------------------------
# AGENTS.md 절대 규칙: 원문에 없는 사실을 추가하지 않는다.
# LLM 경로가 모두 실패하면 내용을 지어내지 않고 반드시 예외로 중단해야 한다.

@patch("llm_gateway.openai_gateway.subprocess.run")
@patch("llm_gateway.openai_gateway.OpenAI")
def test_request_chat_raises_instead_of_fabricating(mock_openai_class, mock_run):
    mock_openai_class.side_effect = RuntimeError("API down")
    mock_run.side_effect = OSError("agy not found")

    gateway = OpenAILikeGateway()
    with pytest.raises(LLMUnavailableError) as exc_info:
        gateway.request_chat(
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="o4-mini",
            messages=[{"role": "user", "content": "주간리뷰 작성"}],
        )

    assert "모든 LLM 경로가 실패" in str(exc_info.value)


@patch("llm_gateway.openai_gateway.subprocess.run")
@patch("llm_gateway.openai_gateway.OpenAI")
def test_request_structured_chat_raises_instead_of_fabricating(mock_openai_class, mock_run):
    mock_openai_class.side_effect = RuntimeError("API down")
    mock_run.side_effect = OSError("agy not found")

    gateway = OpenAILikeGateway()
    with pytest.raises(LLMUnavailableError):
        gateway.request_structured_chat(
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="o4-mini",
            messages=[{"role": "user", "content": "리서치 원문"}],
            response_format=TestSchema,
        )


def test_no_rule_based_fabrication_helpers_remain():
    """규칙 기반 내용 생성 헬퍼가 재도입되지 않았는지 확인한다."""
    gateway = OpenAILikeGateway()
    for forbidden in (
        "_rule_based_fallback_text",
        "_rule_based_weekly_review_fallback",
    ):
        assert not hasattr(gateway, forbidden), (
            f"{forbidden} 이(가) 재도입되었습니다. "
            "원문 근거 없는 내용을 문서에 기록하게 되므로 허용되지 않습니다."
        )


@patch("llm_gateway.openai_gateway.subprocess.run")
@patch("llm_gateway.openai_gateway.OpenAI")
def test_agy_fallback_timeout_is_configurable(mock_openai_class, mock_run, monkeypatch):
    """agy 폴백 타임아웃이 환경변수로 주입되는지 확인한다 (하드코딩 금지 룰)."""
    monkeypatch.setenv("ANTIGRAVITY_TIMEOUT_SECONDS", "45")
    mock_openai_class.side_effect = RuntimeError("API down")
    mock_run.return_value = MagicMock(stdout="응답 텍스트")

    gateway = OpenAILikeGateway()
    res = gateway.request_chat(
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        model="o4-mini",
        messages=[{"role": "user", "content": "hi"}],
    )

    assert res == "응답 텍스트"
    assert mock_run.call_args.kwargs["timeout"] == 45

