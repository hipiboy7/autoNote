import json

import pytest
from agy_gateway import WorkerResult, WorkerRunner, WorkerSpec, WorkerUnavailableError
from analyzer.grounding import ungrounded_numbers
from analyzer.service import (
    THEMES,
    LLMAnalysisError,
    ResearchAnalyzer,
    ResearchReport,
)

SOURCE_765 = (
    "이번 fomc 회의에서 9명중 3명이 금리인상 의견 제시.\n"
    "코스피는 이미 고점대비 40% 이상 빠진 상황. 전자닉스는 40~50% 이상 하락.\n"
    "유일하게 호재는 주식들이 정말 많이 저평가 구간까지 갔다는 정도."
)
SOURCE_766 = (
    "HBM3E 12단 퀄 테스트 통과. 공급 부족이 지속되는 국면.\n"
    "장기공급계약 비중이 늘어 현물 물량이 줄고 있다.\n"
    "다만 중국 수요 둔화는 변수다."
)


def report_dict(message_id, theme="금리", evidence=None, main_claim="결론"):
    return {
        "message_id": message_id,
        "main_claim": main_claim,
        "evidence": evidence if evidence is not None else ["9명중 3명이 금리인상 의견"],
        "logic": "저자는 금리 이슈가 근본 원인이라고 판단했다.",
        "risks": ["미국 증시 변동성에 휘둘릴 가능성"],
        "critical_question": "금리 인상 의견이 실제 인상으로 이어질까?",
        "theme": theme,
    }


class ScriptedRunner(WorkerRunner):
    """호출 순서대로 준비된 응답을 돌려주는 러너."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []
        self.schemas = []

    def run(self, spec, prompt, schema=None):
        self.prompts.append(prompt)
        self.schemas.append(schema)
        if not self.responses:
            raise WorkerUnavailableError("준비된 응답이 없습니다.")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return WorkerResult(
            structured=nxt,
            usage={"total_tokens": 22205},
            worker=spec.name,
            model=spec.model,
        )


def spec(batch_size=4, max_prompt_chars=11000):
    return WorkerSpec(
        name="extract",
        model="gemini-3.1-pro-high",
        batch_size=batch_size,
        max_prompt_chars=max_prompt_chars,
    )


# ------------------------------------------------------------------ 정상 경로

def test_analyze_batch_returns_reports_keyed_by_message_id():
    runner = ScriptedRunner([
        {"reports": [report_dict(765), report_dict(766, theme="반도체사이클",
                                                   evidence=["HBM3E 12단 퀄 테스트 통과"])]}
    ])
    analyzer = ResearchAnalyzer(runner, spec())

    out = analyzer.analyze_batch([(765, SOURCE_765), (766, SOURCE_766)])

    assert set(out) == {765, 766}
    assert out[765].theme == "금리"
    assert out[766].theme == "반도체사이클"
    # 두 건이 한 번의 호출로 처리되어야 한다 (고정 오버헤드 amortize)
    assert len(runner.prompts) == 1
    assert "message_id: 765" in runner.prompts[0]
    assert "message_id: 766" in runner.prompts[0]


def test_schema_is_passed_to_worker_with_theme_enum():
    """theme 이 enum 으로 강제되지 않으면 존재하지 않는 테마가 생성된다 (실측: '금지')."""
    runner = ScriptedRunner([{"reports": [report_dict(1)]}])
    ResearchAnalyzer(runner, spec()).analyze_batch([(1, SOURCE_765)])

    schema = runner.schemas[0]
    dumped = json.dumps(schema, ensure_ascii=False)
    assert "enum" in dumped
    for theme in THEMES:
        assert theme in dumped


def test_analyze_message_single_still_works():
    runner = ScriptedRunner([{"reports": [report_dict(0)]}])
    report = ResearchAnalyzer(runner, spec()).analyze_message(SOURCE_765)
    assert isinstance(report, ResearchReport)
    assert report.theme == "금리"


# ------------------------------------------------------- 배치 조립 (프로그램 책임)

def test_chunks_respect_batch_size():
    runner = ScriptedRunner([
        {"reports": [report_dict(i) for i in (1, 2)]},
        {"reports": [report_dict(i) for i in (3, 4)]},
    ])
    analyzer = ResearchAnalyzer(runner, spec(batch_size=2))

    out = analyzer.analyze_batch([(i, SOURCE_765) for i in (1, 2, 3, 4)])

    assert set(out) == {1, 2, 3, 4}
    assert len(runner.prompts) == 2


def test_chunks_respect_prompt_char_budget():
    """명령행 길이 한계를 넘기지 않도록 프로그램이 미리 쪼갠다."""
    long_source = "숫자 없는 아주 긴 원문입니다. " * 200
    runner = ScriptedRunner([
        {"reports": [report_dict(1, evidence=["근거 없음"])]},
        {"reports": [report_dict(2, evidence=["근거 없음"])]},
    ])
    analyzer = ResearchAnalyzer(runner, spec(batch_size=10, max_prompt_chars=4000),
                                max_source_chars=1500)

    out = analyzer.analyze_batch([(1, long_source), (2, long_source)])

    assert set(out) == {1, 2}
    assert len(runner.prompts) == 2
    for prompt in runner.prompts:
        assert len(prompt) <= 4000


def test_long_source_is_clipped():
    runner = ScriptedRunner([{"reports": [report_dict(1, evidence=["근거"])]}])
    analyzer = ResearchAnalyzer(runner, spec(), max_source_chars=50)

    analyzer.analyze_batch([(1, "가" * 500)])

    assert "원문 일부 생략" in runner.prompts[0]


# --------------------------------------------------- 근거 검증 (창작 차단, 토큰 0)

def test_ungrounded_number_in_evidence_is_rejected():
    """원문에 없는 숫자를 근거로 쓰면 재시도하고, 끝내 실패하면 기록하지 않는다."""
    bad = {"reports": [report_dict(765, evidence=["LTA 비중이 70%에 달한다"])]}
    runner = ScriptedRunner([bad, bad])
    analyzer = ResearchAnalyzer(runner, spec())

    with pytest.raises(LLMAnalysisError) as exc:
        analyzer.analyze_batch([(765, SOURCE_765)])

    assert "765" in str(exc.value)
    assert len(runner.prompts) == 2  # 최초 + 재시도 1회


def test_retry_recovers_when_second_attempt_is_grounded():
    bad = {"reports": [report_dict(765, evidence=["유가가 100달러를 넘었다"])]}
    good = {"reports": [report_dict(765, evidence=["코스피는 고점대비 40% 이상 하락"])]}
    runner = ScriptedRunner([bad, good])

    out = ResearchAnalyzer(runner, spec()).analyze_batch([(765, SOURCE_765)])

    assert out[765].evidence == ["코스피는 고점대비 40% 이상 하락"]


def test_fabrication_marker_is_rejected():
    """과거 창작 폴백 고정 문구가 재출현하면 통과시키지 않는다."""
    bad = {"reports": [report_dict(
        765,
        evidence=["9명중 3명이 금리인상 의견"],
        main_claim="금리 분야 전략적 판단을 도출함",
    )]}
    runner = ScriptedRunner([bad, bad])

    with pytest.raises(LLMAnalysisError):
        ResearchAnalyzer(runner, spec()).analyze_batch([(765, SOURCE_765)])


def test_invalid_theme_is_rejected_by_schema_validation():
    """스키마를 우회해 없는 테마가 와도 pydantic 이 막는다."""
    bad = {"reports": [report_dict(765, theme="금지")]}
    runner = ScriptedRunner([bad, bad])

    with pytest.raises(LLMAnalysisError):
        ResearchAnalyzer(runner, spec()).analyze_batch([(765, SOURCE_765)])


# ----------------------------------------------------------- 누락/오류 처리

def test_missing_message_in_response_is_retried():
    partial = {"reports": [report_dict(765)]}
    rest = {"reports": [report_dict(766, theme="반도체사이클",
                                    evidence=["HBM3E 12단 퀄 테스트 통과"])]}
    runner = ScriptedRunner([partial, rest])

    out = ResearchAnalyzer(runner, spec()).analyze_batch([(765, SOURCE_765), (766, SOURCE_766)])

    assert set(out) == {765, 766}


def test_unrequested_message_id_is_discarded():
    resp = {"reports": [report_dict(765), report_dict(999)]}
    runner = ScriptedRunner([resp])

    out = ResearchAnalyzer(runner, spec()).analyze_batch([(765, SOURCE_765)])

    assert set(out) == {765}


def test_worker_failure_raises_without_fabricating():
    runner = ScriptedRunner([
        WorkerUnavailableError("agy 실패"),
        WorkerUnavailableError("agy 실패"),
    ])

    with pytest.raises(LLMAnalysisError):
        ResearchAnalyzer(runner, spec()).analyze_batch([(765, SOURCE_765)])


def test_empty_input_returns_empty():
    runner = ScriptedRunner([])
    assert ResearchAnalyzer(runner, spec()).analyze_batch([]) == {}


# ------------------------------------------------------- grounding 단위 테스트

@pytest.mark.parametrize(
    "claim,source,expected",
    [
        ("40% 하락", "고점대비 40% 이상 빠진 상황", set()),
        ("1,000억 규모", "1000억 규모 계약", set()),
        ("70% 비중", "고점대비 40% 하락", {"70"}),
        ("9명중 3명", "9명중 3명이 의견 제시", set()),
        ("1. 첫째 항목", "숫자 없는 원문", set()),          # 목록 번호는 무시
        ("2027년까지", "2026년 전망", {"2027"}),
        # 원문의 목록 번호가 뒤 숫자와 붙는 경우(실측 오탐 사례):
        # "3.11월에 하는 미국 중간 선거" 는 '3.11' 한 토큰으로 잡히므로
        # 원문 쪽 숫자를 부분 숫자열까지 확장해야 '11월' 을 창작으로 오판하지 않는다.
        ("11월 미국 중간 선거", "3.11월에 하는 미국 중간 선거 전에", set()),
        ("40~50% 하락", "전자닉스는 40~50% 이상 하락", set()),
    ],
)
def test_ungrounded_numbers(claim, source, expected):
    assert ungrounded_numbers(claim, source) == expected
