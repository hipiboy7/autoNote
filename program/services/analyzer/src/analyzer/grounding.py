"""추출 결과가 원문에 근거하는지 프로그램으로 검증한다 (토큰 0).

LLM 에게 '창작하지 마라' 고 지시하는 것만으로는 부족하다. 실제로 과거 배치는
원문에 없는 수치("LTA 비중 70%", "유가 100달러 선")를 문서에 기록했다.
근거 항목의 **숫자**는 창작 여부를 가리는 가장 값싸고 확실한 신호이므로,
LLM 을 한 번 더 호출하는 대신 프로그램이 원문과 대조한다.
"""

import re
from typing import Iterable, List, Sequence, Set

# 과거 규칙 기반 창작 폴백이 남긴 고정 문구. 다시 나타나면 즉시 실패시킨다.
FABRICATION_MARKERS: Sequence[str] = (
    "분야 전략적 판단을 도출함",
    "관련 저자 주요 결론 요약",
    "시장 변동성 및 거시경제 지표 변화에 따른 잠재적 위험 관찰 필요",
    "가설이 유지되기 위해 추적해야 할 핵심 지표는 무엇인가",
    "본문 내 주요 수치 데이터 및 팩트 참고",
    "LTA 비중이 70%",
    "유가(100달러 선)",
)

_NUMBER = re.compile(r"\d[\d,\.]*")
_LIST_PREFIX = re.compile(r"^\s*[-*•]?\s*\d+[\.)]\s+")


def _normalize_number(token: str) -> str:
    """1,000 / 1000. / 40.0 을 비교 가능한 형태로 정규화한다."""
    token = token.replace(",", "").rstrip(".")
    if "." in token:
        head, _, tail = token.partition(".")
        tail = tail.rstrip("0")
        token = f"{head}.{tail}" if tail else head
    return token.lstrip("0") or "0"


_DIGIT_RUN = re.compile(r"\d+")


def extract_numbers(
    text: str,
    strip_list_prefix: bool = False,
    expand: bool = False,
) -> Set[str]:
    """텍스트에서 숫자 토큰을 정규화해 추출한다.

    expand=True 면 토큰 안의 개별 숫자열까지 함께 넣는다. 원문 쪽에 쓴다.
    원문의 목록 번호가 뒤 숫자와 붙는 경우("3.11월에 중간선거" → 토큰 '3.11')가 있어,
    확장 없이는 근거의 '11월' 을 창작으로 오판한다.
    """
    if not text:
        return set()
    if strip_list_prefix:
        text = "\n".join(_LIST_PREFIX.sub("", line) for line in text.split("\n"))

    numbers: Set[str] = set()
    for match in _NUMBER.finditer(text):
        token = match.group(0)
        numbers.add(_normalize_number(token))
        if expand:
            for run in _DIGIT_RUN.findall(token):
                numbers.add(_normalize_number(run))
    return numbers


def ungrounded_numbers(claim_text: str, source_text: str) -> Set[str]:
    """근거 문장에 있으나 원문에 없는 숫자를 반환한다.

    원문 쪽은 관대하게(부분 숫자열까지), 근거 쪽은 쓰인 그대로 비교한다.
    창작을 잡는 것이 목적이므로 오탐(정상 글을 버리는 것)을 더 경계한다.
    """
    source = extract_numbers(source_text, expand=True)
    claimed = extract_numbers(claim_text, strip_list_prefix=True)
    return {n for n in claimed if n not in source}


def find_fabrication_markers(texts: Iterable[str]) -> List[str]:
    hits = []
    for text in texts:
        for marker in FABRICATION_MARKERS:
            if marker in (text or ""):
                hits.append(marker)
    return sorted(set(hits))


def check_grounding(
    source_text: str,
    evidence: Sequence[str],
    main_claim: str = "",
    logic: str = "",
    risks: Sequence[str] = (),
    critical_question: str = "",
    allowed_themes: Sequence[str] = (),
    theme: str = "",
) -> List[str]:
    """근거 부족 항목을 문제 목록으로 반환한다. 빈 목록이면 통과.

    - 근거(evidence)는 사실 주장이므로 **엄격**하게 본다: 원문에 없는 숫자는 곧 창작이다.
    - 해석 영역(main_claim/logic/risks)은 저자 논리의 재구성이므로 숫자 검사를
      적용하지 않는다. 대신 과거 창작 문구 재출현만 차단한다.
    """
    problems: List[str] = []

    # 근거가 비어 있는 것은 실패가 아니다. 이 채널에는 수치 없는 짧은 판단 코멘트가
    # 많고("바닥에서는 단기 과열권인듯"), 그런 글의 가치는 판단 논리에 있다.
    # 근거를 강제하면 워커가 없는 수치를 만들어 채우도록 압력을 주는 역효과가 난다.
    # (실측: 2026-08-06 배치가 message 807·809 에서 이 조건으로 중단됐다.)
    if not (main_claim or "").strip():
        problems.append("핵심 주장(main_claim)이 비어 있습니다.")
    if not (logic or "").strip():
        problems.append("판단 논리(logic)가 비어 있습니다.")

    for item in evidence:
        item = (item or "").strip()
        if not item:
            problems.append("빈 근거 항목이 있습니다.")
            continue
        missing = ungrounded_numbers(item, source_text)
        if missing:
            problems.append(
                f"원문에 없는 숫자 {sorted(missing)} 가 근거에 사용되었습니다: {item[:80]}"
            )

    markers = find_fabrication_markers(
        [main_claim, logic, critical_question, *evidence, *risks]
    )
    if markers:
        problems.append(f"과거 창작 폴백 고정 문구가 재출현했습니다: {markers}")

    if allowed_themes and theme and theme not in allowed_themes:
        problems.append(f"허용되지 않은 테마 {theme!r} 입니다.")

    return problems
