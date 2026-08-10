"""리서치 원문에서 판단 논리를 추출하는 분석 서비스.

설계 원칙 (AGENTS.md 3계층):
  * LLM(agy 워커)은 **판단**만 한다: 핵심 주장·근거·논리·리스크·질문·테마.
  * 프로그램은 **정확성과 토큰**을 담당한다: 배치 조립, 스키마 강제, 근거 검증,
    누락 항목 재시도, 실패 시 기록 중단.
  * 테마는 enum 으로 제한한다. 자유 문자열로 두면 존재하지 않는 테마(실측: '금지')가
    생성되고, 라우팅이 조용히 기본 문서로 폴백해 오분류가 쌓인다.
"""

import json
import sys
from typing import Dict, List, Literal, Sequence, Tuple

from agy_gateway import WorkerRunner, WorkerSpec, WorkerUnavailableError
from pydantic import BaseModel, Field

from .grounding import check_grounding

# 테마 목록. 문서 매핑표의 키와 정확히 일치해야 하며 main.py 가 시작 시 검증한다.
THEMES: Tuple[str, ...] = (
    "반도체사이클",
    "시장심리·수급",
    "환율·매크로",
    "금리",
    "방산·방위산업",
    "한화에어로스페이스",
    "에이유브랜즈",
    "에이피알",
)

ThemeName = Literal[
    "반도체사이클",
    "시장심리·수급",
    "환율·매크로",
    "금리",
    "방산·방위산업",
    "한화에어로스페이스",
    "에이유브랜즈",
    "에이피알",
]


class ResearchReport(BaseModel):
    main_claim: str = Field(..., description="저자가 이 글로 최종 전달하려는 결론 한 줄")
    evidence: List[str] = Field(
        ..., description="원문이 제시한 구체적 수치·관찰·사실만. 원문에 없는 숫자 금지"
    )
    logic: str = Field(..., description="근거에서 결론에 이른 저자의 추론 경로")
    risks: List[str] = Field(..., description="원문이 언급한 리스크 및 반대 시나리오")
    critical_question: str = Field(..., description="가설 검증을 위해 추적할 핵심 질문 1개")
    theme: ThemeName = Field(..., description="주제 분류 (목록 중 정확히 하나)")


class BatchReportItem(ResearchReport):
    message_id: int = Field(..., description="분석 대상 메시지 ID")


class BatchReports(BaseModel):
    reports: List[BatchReportItem]


class LLMAnalysisError(Exception):
    """분석이 재시도 후에도 실패했음을 알린다. 호출자는 기록하지 않고 중단해야 한다."""


SYSTEM_PROMPT = (
    "너는 투자 리서치 분석가다. 주어진 원문에서만 근거를 취해 판단 논리를 구조화한다.\n"
    "규칙:\n"
    "1. 원문에 없는 사실·수치·전망을 절대 추가하지 마라. 특히 근거(evidence)의 숫자는\n"
    "   원문에 그대로 등장하는 것만 사용해라. 프로그램이 원문과 대조 검증한다.\n"
    "   원문에 구체적 수치나 관찰 사실이 없으면 evidence 를 빈 배열로 두어라.\n"
    "   빈 배열은 정상이다. 칸을 채우려고 없는 근거를 만들지 마라.\n"
    "2. 저자의 전망을 확정된 사실처럼 쓰지 마라.\n"
    "3. 종목 추천이나 매매 판단을 만들지 마라. 이것은 학습용 기록이다.\n"
    "4. 도구를 쓰거나 파일을 만들지 말고 곧바로 결과만 산출해라.\n"
    f"5. theme 은 반드시 다음 중 하나다: {', '.join(THEMES)}\n"
)


class ResearchAnalyzer:
    def __init__(
        self,
        runner: WorkerRunner,
        spec: WorkerSpec,
        max_source_chars: int = 2400,
        max_retries: int = 1,
    ):
        self.runner = runner
        self.spec = spec
        self.max_source_chars = max_source_chars
        self.max_retries = max_retries

    # ------------------------------------------------------------ 배치 인터페이스

    def analyze_batch(self, items: Sequence[Tuple[int, str]]) -> Dict[int, ResearchReport]:
        """(message_id, 원문) 목록을 배치로 분석한다.

        고정 오버헤드(호출당 input 약 20.7k 토큰)를 여러 건에 분산시키는 것이 목적이다.
        모든 항목이 근거 검증까지 통과해야 결과를 반환한다.
        """
        if not items:
            return {}

        results: Dict[int, ResearchReport] = {}
        for chunk in self._chunk(items):
            results.update(self._analyze_chunk(chunk))
        return results

    def analyze_message(self, text: str) -> ResearchReport:
        """단건 분석 (하위 호환). 배치가 가능하면 analyze_batch 가 저렴하다."""
        reports = self.analyze_batch([(0, text)])
        if 0 not in reports:
            raise LLMAnalysisError("단건 분석 결과를 얻지 못했습니다.")
        return reports[0]

    # -------------------------------------------------------------------- 내부

    def _chunk(self, items: Sequence[Tuple[int, str]]) -> List[List[Tuple[int, str]]]:
        """배치 크기와 프롬프트 길이 예산을 함께 지켜 청크를 만든다."""
        budget = self.spec.max_prompt_chars - len(SYSTEM_PROMPT) - 500
        chunks: List[List[Tuple[int, str]]] = []
        current: List[Tuple[int, str]] = []
        used = 0

        for msg_id, text in items:
            body = self._clip(text)
            cost = len(body) + 60  # 구분자 및 message_id 표기 여유
            if current and (len(current) >= self.spec.batch_size or used + cost > budget):
                chunks.append(current)
                current, used = [], 0
            current.append((msg_id, body))
            used += cost

        if current:
            chunks.append(current)
        return chunks

    def _clip(self, text: str) -> str:
        text = (text or "").strip()
        if len(text) <= self.max_source_chars:
            return text
        return text[: self.max_source_chars] + "\n…(원문 일부 생략)"

    def _build_prompt(self, chunk: Sequence[Tuple[int, str]]) -> str:
        parts = [SYSTEM_PROMPT, "\n[분석 대상] 각 항목을 message_id 별로 하나씩 분석해라."]
        for msg_id, body in chunk:
            parts.append(f"\n--- message_id: {msg_id} ---\n{body}")
        parts.append(
            f"\n\nreports 배열에 정확히 {len(chunk)}개 항목을 담아라. "
            "각 항목의 message_id 는 위에 표시된 값을 그대로 써라."
        )
        return "\n".join(parts)

    def _analyze_chunk(self, chunk: Sequence[Tuple[int, str]]) -> Dict[int, ResearchReport]:
        sources = {msg_id: body for msg_id, body in chunk}
        pending = list(sources)
        collected: Dict[int, ResearchReport] = {}
        schema = BatchReports.model_json_schema()

        attempt = 0
        last_error = ""
        while pending and attempt <= self.max_retries:
            attempt += 1
            subset = [(mid, sources[mid]) for mid in pending]
            try:
                result = self.runner.run(self.spec, self._build_prompt(subset), schema)
                payload = result.structured
                if not isinstance(payload, dict):
                    payload = json.loads(str(payload))
                batch = BatchReports.model_validate(payload)
            except (WorkerUnavailableError, ValueError, json.JSONDecodeError) as e:
                last_error = str(e)[:300]
                print(
                    f"[ResearchAnalyzer] 배치 분석 실패 (시도 {attempt}): {last_error}",
                    file=sys.stderr,
                )
                continue

            still_pending: List[int] = []
            returned = {item.message_id for item in batch.reports}
            for msg_id in pending:
                if msg_id not in returned:
                    print(
                        f"[ResearchAnalyzer] message {msg_id} 결과가 누락되어 재시도합니다.",
                        file=sys.stderr,
                    )
                    still_pending.append(msg_id)

            for item in batch.reports:
                if item.message_id not in sources:
                    print(
                        f"[ResearchAnalyzer] 요청하지 않은 message_id {item.message_id} "
                        "응답을 버립니다.",
                        file=sys.stderr,
                    )
                    continue

                report = ResearchReport(
                    main_claim=item.main_claim,
                    evidence=item.evidence,
                    logic=item.logic,
                    risks=item.risks,
                    critical_question=item.critical_question,
                    theme=item.theme,
                )
                problems = check_grounding(
                    source_text=sources[item.message_id],
                    evidence=report.evidence,
                    main_claim=report.main_claim,
                    logic=report.logic,
                    risks=report.risks,
                    critical_question=report.critical_question,
                    allowed_themes=THEMES,
                    theme=report.theme,
                )
                if problems:
                    last_error = f"근거 검증 실패: {problems}"
                    print(
                        f"[ResearchAnalyzer] message {item.message_id} {last_error}",
                        file=sys.stderr,
                    )
                    if item.message_id not in still_pending:
                        still_pending.append(item.message_id)
                    continue

                collected[item.message_id] = report

            pending = still_pending

        if pending:
            raise LLMAnalysisError(
                f"message {pending} 분석이 재시도 후에도 통과하지 못했습니다. "
                f"마지막 원인: {last_error}. "
                "원문에 근거하지 않은 내용을 기록하지 않기 위해 중단합니다."
            )
        return collected
