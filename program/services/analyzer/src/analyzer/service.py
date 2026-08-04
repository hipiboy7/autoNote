from typing import List
from pydantic import BaseModel, Field
from llm_gateway import LLMGateway
from .config import LLMSettings

class ResearchReport(BaseModel):
    main_claim: str = Field(
        ..., 
        description="저자가 이 글을 통해 최종적으로 전달하려는 결론 한 줄 요약"
    )
    evidence: List[str] = Field(
        ..., 
        description="주장을 뒷받침하기 위해 본문에서 제시된 구체적인 수치, 관찰 데이터, 팩트 목록"
    )
    logic: str = Field(
        ..., 
        description="저자가 어떤 가정을 토대로 왜 그러한 해석과 결과를 도출했는지 추론 경로 분석"
    )
    risks: List[str] = Field(
        ..., 
        description="원문에서 제기된 리스크 및 사용자가 독립적으로 생각해볼 만한 잠재적 반대 시나리오"
    )
    critical_question: str = Field(
        ..., 
        description="본문 가설의 신뢰성을 검증하기 위해 사용자가 추가 리서치해보아야 할 핵심 질문 1개"
    )
    theme: str = Field(
        ...,
        description="이 리서치가 속하는 주제 분류. 다음 중 하나를 정확히 선택: "
                    "반도체사이클, 시장심리·수급, 환율·매크로, 금리, 방산·방위산업, "
                    "한화에어로스페이스, 에이유브랜즈, 에이피알"
    )

class LLMAnalysisError(Exception):
    """LLM 분석이 최대 재시도 후에도 실패할 때 발생하는 예외"""
    pass

class ResearchAnalyzer:
    def __init__(self, gateway: LLMGateway, settings: LLMSettings):
        self.gateway = gateway
        self.settings = settings

    def analyze_message(self, text: str) -> ResearchReport:
        # 시스템 프롬프트 및 유저 프롬프트 구축
        system_prompt = (
            "너는 투자 리서치 분석가다. 입력된 주식 리서치 코멘트를 파싱하여, "
            "핵심 주장, 근거 데이터, 판단의 논리, 리스크, critical한 질문을 균형있게 추출하고, "
            "가장 적절한 테마를 분류해라. "
            "사용 가능한 테마: 반도체사이클, 시장심리·수급, 환율·매크로, 금리, 방산·방위산업, "
            "한화에어로스페이스, 에이유브랜즈, 에이피알. "
            "원문에 없는 전망을 확정적 사실처럼 쓰지 말고, 추론은 명확히 논리 항목으로 분류해라. "
            "반드시 정해진 JSON Schema 포맷을 엄격하게 준수하여 응답해라."
        )
        
        user_prompt = f"아래 리서치 원문을 정밀하게 분석해라.\n\n[원문]\n{text}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                # reasoning-llm을 기본 분석용 모델로 사용
                report = self.gateway.request_structured_chat(
                    base_url=self.settings.reasoning_llm_base_url,
                    api_key=self.settings.reasoning_llm_api_key,
                    model=self.settings.reasoning_llm_model,
                    messages=messages,
                    response_format=ResearchReport,
                    temperature=0.1
                )
                return report
            except Exception as e:
                print(f"LLM analysis validation error on attempt {attempt + 1}: {e}")
                if attempt == max_retries:
                    raise LLMAnalysisError(f"LLM analysis failed after max retries. Error: {e}") from e
                
                # 재시도시 교정 가이드를 유저 프롬프트 뒤에 덧붙임
                messages.append({"role": "assistant", "content": "오류가 발생하여 올바른 형식의 응답을 생성하지 못했습니다."})
                messages.append({
                    "role": "user", 
                    "content": "다시 분석해 주세요. 특히 모든 필드(main_claim, evidence, logic, risks, critical_question)가 누락 없이 올바른 JSON 구조로 채워져 있는지 반드시 확인해 주시기 바랍니다."
                })
