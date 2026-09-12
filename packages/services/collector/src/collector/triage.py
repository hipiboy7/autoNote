"""리서치 여부 판별.

기존 구현은 'URL 제거 후 3줄 이하면 리서치 아님' 규칙을 썼다. 실측 결과 이 채널
최근 200건 중 118건(59%)이 이 규칙에 걸려 탈락했고, 그중에는 사이드카·수급 판단처럼
짧지만 핵심적인 코멘트가 포함되어 있었다(2026-08-04 r753·r755 사례: 사람이 수동으로
따라 기록해야 했다). 줄 수는 리서치 가치의 지표가 아니다.

새 구조:
  * 프로그램은 **확실한 것만** 걸러낸다 (본문 없음, 링크만, 광고·공지). 토큰 0.
  * 애매한 것은 triage 워커가 배치로 판별한다. 출력이 작아 배치를 크게 잡을 수 있다.
"""

import sys
from typing import Dict, List, Optional, Sequence, Set

from agy_gateway import WorkerRunner, WorkerSpec, WorkerUnavailableError
from pydantic import BaseModel, Field
from telegram_client import TelegramMessage

import re

URL_PATTERN = re.compile(r"https?://[^\s]+")

# 본문이 없는 미디어 전용 메시지에서 텔레그램 MCP 가 채워 넣는 표식
EMPTY_MARKERS = ("", "[empty]")

# 명백히 리서치가 아닌 글 (프로그램이 확정 판단)
SKIP_KEYWORDS = (
    "광고", "제휴", "공구", "쿠팡 파트너스",
    "공지사항", "[공지]", "★공지", "투표", "설문조사",
)

REASON_NO_BODY = "본문 없음(미디어 전용)"
REASON_LINK_ONLY = "링크만 있음"
REASON_KEYWORD = "광고·공지 키워드"
# triage 워커가 실패했거나 결과를 누락해 보수적 기본값으로 채운 경우.
# 검증 없이 기록된 글이 있다는 뜻이므로 main 이 이 건수를 성공 알림에 실는다.
REASON_FALLBACK = "판별 결과 누락 — 보수적 기본값 적용"


class TriageDecision(BaseModel):
    message_id: int = Field(..., description="판별 대상 메시지 ID")
    is_research: bool = Field(..., description="투자 판단·분석이 담긴 글이면 true")
    reason: str = Field(..., description="판단 근거 한 줄")


class TriageBatch(BaseModel):
    decisions: List[TriageDecision]


TRIAGE_PROMPT = (
    "너는 투자 리서치 채널의 글을 선별하는 사서다.\n"
    "각 글이 '투자 판단·분석·근거가 담긴 리서치'인지 판별해라.\n\n"
    "리서치로 볼 것(true):\n"
    "  - 시장·종목·지표에 대한 저자의 해석이나 판단이 담긴 글 (한두 줄이어도 무조건 true)\n"
    "  - 수급·지수·환율·금리 관찰과 그에 대한 코멘트\n"
    "  - 짧아도 '왜 그렇게 보는지' 또는 '어떻게 대응할지'가 드러나는 글\n\n"
    "리서치가 아닌 것(false):\n"
    "  - 인사, 잡담, 이모지만 있는 글\n"
    "  - 설명 없는 단순 링크·기사 제목 공유\n"
    "  - 광고, 공지, 이벤트, 투표\n\n"
    "글이 짧다는 이유만으로 false 로 판정하지 마라. 판단이 담겼는지가 기준이다.\n"
    "도구를 쓰거나 파일을 만들지 말고 곧바로 결과만 산출해라.\n"
)


class ResearchTriage:
    """프로그램 사전 필터 + triage 워커 배치 판별."""

    def __init__(
        self,
        runner: WorkerRunner,
        spec: WorkerSpec,
        max_preview_chars: int = 500,
        include_on_failure: bool = True,
    ):
        self.runner = runner
        self.spec = spec
        self.max_preview_chars = max_preview_chars
        # 워커 장애 시 보수적으로 '포함' 처리한다. 분석 단계에서 다시 검증되므로
        # 글을 잃는 것보다 안전하다.
        self.include_on_failure = include_on_failure
        # 직전 classify() 에서 워커 판별 없이 기본값으로 처리된 message_id.
        # 워커가 실패하면 후보 전원이 여기 들어온다 (2026-09-04 실제 발생).
        self.fallback_ids: Set[int] = set()

    # ------------------------------------------------- 프로그램 확정 판단 (토큰 0)

    @staticmethod
    def obvious_skip_reason(msg: TelegramMessage) -> Optional[str]:
        text = (msg.text or "").strip()
        if text in EMPTY_MARKERS:
            return REASON_NO_BODY

        if any(keyword in text for keyword in SKIP_KEYWORDS):
            return REASON_KEYWORD

        without_urls = URL_PATTERN.sub("", text).strip()
        if not without_urls:
            return REASON_LINK_ONLY

        return None

    # -------------------------------------------------------- 워커 배치 판별

    def classify(self, messages: Sequence[TelegramMessage]) -> Dict[int, TriageDecision]:
        """사전 필터를 통과한 메시지에 대해 리서치 여부를 판별한다."""
        decisions: Dict[int, TriageDecision] = {}
        candidates: List[TelegramMessage] = []
        self.fallback_ids = set()

        for msg in messages:
            reason = self.obvious_skip_reason(msg)
            if reason:
                decisions[msg.id] = TriageDecision(
                    message_id=msg.id, is_research=False, reason=reason
                )
            else:
                candidates.append(msg)

        for chunk in self._chunk(candidates):
            decisions.update(self._classify_chunk(chunk))

        return decisions

    def _chunk(self, messages: Sequence[TelegramMessage]) -> List[List[TelegramMessage]]:
        budget = self.spec.max_prompt_chars - len(TRIAGE_PROMPT) - 400
        chunks: List[List[TelegramMessage]] = []
        current: List[TelegramMessage] = []
        used = 0

        for msg in messages:
            cost = min(len(msg.text or ""), self.max_preview_chars) + 60
            if current and (len(current) >= self.spec.batch_size or used + cost > budget):
                chunks.append(current)
                current, used = [], 0
            current.append(msg)
            used += cost

        if current:
            chunks.append(current)
        return chunks

    def _build_prompt(self, chunk: Sequence[TelegramMessage]) -> str:
        parts = [TRIAGE_PROMPT, "[판별 대상]"]
        for msg in chunk:
            preview = (msg.text or "").strip()[: self.max_preview_chars]
            parts.append(f"\n--- message_id: {msg.id} ---\n{preview}")
        parts.append(
            f"\n\ndecisions 배열에 정확히 {len(chunk)}개 항목을 담아라. "
            "message_id 는 위 값을 그대로 써라."
        )
        return "\n".join(parts)

    def _classify_chunk(
        self, chunk: Sequence[TelegramMessage]
    ) -> Dict[int, TriageDecision]:
        requested = {msg.id for msg in chunk}
        try:
            result = self.runner.run(
                self.spec, self._build_prompt(chunk), TriageBatch.model_json_schema()
            )
            payload = result.structured
            batch = TriageBatch.model_validate(payload)
            decisions = {
                d.message_id: d for d in batch.decisions if d.message_id in requested
            }
        except (WorkerUnavailableError, ValueError) as e:
            print(
                f"[ResearchTriage] 판별 실패: {str(e)[:300]}. "
                f"{'포함' if self.include_on_failure else '제외'}으로 처리합니다.",
                file=sys.stderr,
            )
            decisions = {}

        # 누락된 항목은 보수적 기본값으로 채운다 (글을 잃지 않는 쪽)
        for msg_id in requested - set(decisions):
            self.fallback_ids.add(msg_id)
            decisions[msg_id] = TriageDecision(
                message_id=msg_id,
                is_research=self.include_on_failure,
                reason=REASON_FALLBACK,
            )
        return decisions
