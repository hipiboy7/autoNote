import pytest
from datetime import datetime, timezone, timedelta
from google_docs_client import GoogleDocsClient
from agy_gateway import WorkerResult, WorkerRunner, WorkerSpec, WorkerUnavailableError
from reviewer.parser import WeeklyReviewParser, ParsedRecord
from reviewer.service import WeeklyReviewer, strip_markdown

class MockGoogleDocsClient(GoogleDocsClient):
    def __init__(self, doc_contents: dict[str, str] = None):
        self.doc_contents = doc_contents or {}
        self.overwrite_log = {}
        self.append_log = []
        self.sepia_applied = []

    def append_text(self, doc_id: str, text: str) -> bool:
        # 실제 Docs 처럼 문서 맨 아래에 덧붙인다 (누적 동작 검증을 위해 필요)
        self.doc_contents[doc_id] = self.doc_contents.get(doc_id, "") + text
        self.append_log.append(doc_id)
        return True

    def read_text(self, doc_id: str) -> str:
        return self.doc_contents.get(doc_id, "")

    def overwrite_text(self, doc_id: str, text: str) -> bool:
        self.doc_contents[doc_id] = text
        self.overwrite_log[doc_id] = text
        return True

    def replace_line(self, doc_id: str, find_text: str, replace_text: str) -> bool:
        current = self.doc_contents.get(doc_id, "")
        if not find_text or "\n" in find_text or find_text not in current:
            return False
        self.doc_contents[doc_id] = current.replace(find_text, replace_text)
        return True

    def create_doc(self, title: str, content: str = "", folder_id: str = "") -> str:
        doc_id = f"created::{title}"
        self.doc_contents[doc_id] = content
        return doc_id

    def apply_sepia_theme(self, doc_id: str) -> bool:
        self.sepia_applied.append(doc_id)
        return True

    def batch_update(self, doc_id: str, requests: list) -> bool:
        return True

REVIEW_SPEC = WorkerSpec(name="review", model="claude-opus-4-6-thinking",
                        max_prompt_chars=24000)


class MockWorkerRunner(WorkerRunner):
    def __init__(self, response_text="", fail=False):
        self.response_text = response_text
        self.fail = fail
        self.calls = 0
        self.prompts = []

    def run(self, spec, prompt, schema=None):
        self.calls += 1
        self.prompts.append(prompt)
        if self.fail:
            raise WorkerUnavailableError("review 워커 실패")
        return WorkerResult(
            structured=self.response_text,
            raw_response=self.response_text,
            worker=spec.name,
            model=spec.model,
        )

def test_weekly_review_parser_extracts_recent_records():
    # 기준 시점: 2026-07-18 (토요일)
    # 최근 7일(7/12 ~ 7/18) 이내 글만 파싱되고, 7/11 이전 글은 배제되어야 함
    doc_text = (
        "━━━━━━━━━━━━━━━━━━\n"
        "■ [t305] 2026-07-15 | 삼성전자 HBM 실적 전망\n"
        "▶ 출처: 피터케이 채널\n"
        "▶ 핵심 주장: 퀄 테스트 통과에 따른 이익 급증\n"
        "▶ 근거·데이터:\n"
        "  - 근거 1 내용\n"
        "  - 근거 2 내용\n"
        "▶ 판단 논리: 공급 정상화를 통한 마진 회복\n"
        "▶ 리스크·반대 시나리오:\n"
        "  - 퀄 지연 리스크\n"
        "▶ 💭 내 생각: 단기 매수 기회 판단\n"
        "▶ ❓ 생각해볼 질문: 다변화 공급망 여부?\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "■ [s304] 2026-07-11 | 한화에어로 방산 분석 (7일 이전 글)\n"
        "▶ 출처: 피터케이 채널\n"
        "▶ 핵심 주장: 수주 증가 전망\n"
        "▶ 근거·데이터:\n"
        "  - 근거 1\n"
        "▶ 판단 논리: 지정학적 긴장\n"
        "▶ 리스크·반대 시나리오:\n"
        "  - 수주 지연\n"
        "▶ 💭 내 생각: 보유 비중 유지\n"
        "▶ ❓ 생각해볼 질문: 마진 회복 시점?\n"
    )

    parser = WeeklyReviewParser()
    base_date = datetime(2026, 7, 18, 12, 0, 0)
    records = parser.parse_recent_records(doc_text, base_date)

    # 7/15 글은 범위 내, 7/11 글은 제외되어 1건만 수집되어야 함
    assert len(records) == 1
    rec = records[0]
    assert rec.identifier == "t305"
    assert rec.date_str == "2026-07-15"
    assert rec.title == "삼성전자 HBM 실적 전망"
    assert rec.main_claim == "퀄 테스트 통과에 따른 이익 급증"
    assert len(rec.evidence) == 2
    assert rec.my_thought == "단기 매수 기회 판단"

def test_weekly_reviewer_generates_and_saves_review():
    theme_doc_id = "theme_10"
    theme_content = (
        "━━━━━━━━━━━━━━━━━━\n"
        "■ 2026-07-15 | 삼성전자 HBM 실적 전망\n"
        "▶ 식별자: [t305]\n"
        "▶ 출처: 피터케이 채널\n"
        "▶ 핵심 주장: 이익 급증\n"
        "▶ 근거·데이터:\n"
        "  - 근거 1\n"
        "▶ 판단 논리: 마진 회복\n"
        "▶ 리스크·반대 시나리오:\n"
        "  - 퀄 지연\n"
        "▶ 💭 내 생각: 매수 대응\n"
        "▶ ❓ 생각해볼 질문: 다변화 가능 여부?\n"
    )
    
    doc_contents = {
        theme_doc_id: theme_content,
        "review_doc_2026-W29": ""  # 주간 리뷰 문서
    }
    doc_mappings = {
        "반도체사이클": theme_doc_id
    }

    client = MockGoogleDocsClient(doc_contents)
    gateway = MockWorkerRunner(response_text="## 주간 리서치 성찰 요약서\n1. 핵심 논리: ...")
    
    reviewer = WeeklyReviewer(
        docs_client=client,
        runner=gateway,
        spec=REVIEW_SPEC,
        doc_mappings=doc_mappings,
    )

    # 기준 시점: 2026-07-18 (토요일, 2026년 29주차)
    base_date = datetime(2026, 7, 18, 12, 0, 0)
    
    # 주간리뷰 실행
    review_doc_id = "review_doc_2026-W29"
    res = reviewer.generate_weekly_review(review_doc_id, base_date)

    assert res is True
    assert gateway.calls == 1
    
    # 리뷰 문서 갱신 확인
    updated_review = client.read_text(review_doc_id)
    assert "주간 리서치 성찰 요약서" in updated_review
    assert "o4-mini" in client.sepia_applied or review_doc_id in client.sepia_applied

def test_weekly_review_accumulates_and_preserves_past_reviews():
    """AGENTS.md: 40_리뷰 는 단일 통합 문서에 누적(append)한다.

    덮어쓰면 지난주 리뷰가 사라져 관점 변화 추적이 불가능해진다.
    """
    theme_doc_id = "theme_10"
    theme_content = (
        "━━━━━━━━━━━━━━━━━━\n"
        "■ [t310] 2026-07-15 | 삼성전자 HBM 실적 전망\n"
        "▶ 핵심 주장: 퀄 테스트 통과에 따른 이익 급증\n"
        "▶ 근거·데이터:\n  - HBM3E 12단 공급\n"
        "▶ 판단 논리: 공급 부족 지속\n"
        "▶ 리스크·반대 시나리오:\n  - 중국 수요 둔화\n"
        "▶ 💭 내 생각: 단기 매수 기회 판단\n"
        "▶ ❓ 생각해볼 질문: 다변화 가능 여부?\n"
    )

    review_doc_id = "review_doc"
    previous_review = (
        "━━━━━━━━━━━━━━━━━━\n"
        "■ 2026-07-11 주간리뷰 성찰 보고서 (2026-W28)\n"
        "지난주에 기록된 소중한 리뷰 본문\n"
    )

    client = MockGoogleDocsClient({
        theme_doc_id: theme_content,
        review_doc_id: previous_review,
    })
    gateway = MockWorkerRunner(response_text="## 이번 주 핵심 논리\n**첫째**, 공급 부족.\n---\n끝.")

    reviewer = WeeklyReviewer(
        docs_client=client,
        runner=gateway,
        spec=REVIEW_SPEC,
        doc_mappings={"반도체사이클": theme_doc_id},
    )

    assert reviewer.generate_weekly_review(review_doc_id, datetime(2026, 7, 18, 12, 0, 0)) is True

    final = client.read_text(review_doc_id)
    # 1. 지난주 리뷰가 보존되어야 한다
    assert "지난주에 기록된 소중한 리뷰 본문" in final
    # 2. 이번 주 리뷰가 아래에 덧붙어야 한다
    assert "이번 주 핵심 논리" in final
    assert final.index("지난주에 기록된") < final.index("이번 주 핵심 논리")
    # 3. 덮어쓰기는 호출되지 않아야 한다
    assert client.overwrite_log == {}
    # 4. 마크다운 기호가 본문에 노출되지 않아야 한다
    assert "##" not in final
    assert "**" not in final


def test_weekly_reviewer_skips_when_no_recent_records():
    # 최근 7일 내의 메시지가 아예 존재하지 않는 경우
    theme_doc_id = "theme_10"
    theme_content = (
        "━━━━━━━━━━━━━━━━━━\n"
        "■ 2026-07-05 | 옛날 분석 글\n"
        "▶ 식별자: [t300]\n"
        "▶ 핵심 주장: 예전 얘기\n"
        "▶ 💭 내 생각: 미기록\n"
    )
    
    doc_contents = {
        theme_doc_id: theme_content,
        "review_doc_2026-W29": ""
    }
    doc_mappings = {"t": theme_doc_id}

    client = MockGoogleDocsClient(doc_contents)
    gateway = MockWorkerRunner()
    
    reviewer = WeeklyReviewer(client, gateway, REVIEW_SPEC, doc_mappings)
    base_date = datetime(2026, 7, 18, 12, 0, 0)
    
    res = reviewer.generate_weekly_review("review_doc_2026-W29", base_date)

    # 신규 리서치가 없더라도 에러 없이 스킵 대체 텍스트를 문서에 기록하고 True 반환 보장
    assert res is True
    assert gateway.calls == 0 # LLM 호출되지 않음
    updated_review = client.read_text("review_doc_2026-W29")
    assert "신규 리서치가 없어 주간리뷰를 생성하지 않습니다" in updated_review

def test_weekly_reviewer_handles_exceptions_and_parses_edge_cases(capsys):
    # 1. 날짜가 깨지거나 헤더가 손상된 본문 데이터 셋업
    theme_content_corrupted = (
        "━━━━━━━━━━━━━━━━━━\n"
        "■ 2026-07-XX | 날짜 포맷 깨진 레코드 (ValueError)\n"
        "▶ 식별자: [t305]\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "헤더가 아예 없는 일반 텍스트 라인\n"
    )
    
    doc_contents = {
        "theme_corrupted": theme_content_corrupted,
        "theme_empty": "", # 비어있는 본문 Docs
        "review_doc": ""
    }
    doc_mappings = {
        "t": "theme_corrupted",
        "s": "theme_empty"
    }

    client = MockGoogleDocsClient(doc_contents)
    gateway = MockWorkerRunner()
    
    reviewer = WeeklyReviewer(client, gateway, REVIEW_SPEC, doc_mappings)
    base_date = datetime(2026, 7, 18)
    
    # 7일 내 수집 데이터가 깨진 항목뿐이므로 신규 리서치가 없는 스킵 경로를 타야 함
    assert reviewer.generate_weekly_review("review_doc", base_date) is True
    assert gateway.calls == 0

    # 2. LLM Gateway 호출 중 예외가 발생하는 경우 검증
    # 정상 데이터가 존재하여 LLM을 호출하는 시나리오 설계
    doc_contents_normal = {
        "theme_normal": (
            "━━━━━━━━━━━━━━━━━━\n"
            "■ 2026-07-15 | 정상 타이틀\n"
            "▶ 식별자: [t305]\n"
            "▶ 핵심 주장: 주장\n"
            "▶ 판단 논리: 논리\n"
            "▶ 💭 내 생각: 생각\n"
        ),
        "review_doc": ""
    }
    doc_mappings_normal = {"t": "theme_normal"}
    
    client_normal = MockGoogleDocsClient(doc_contents_normal)
    faulty_gateway = MockWorkerRunner(fail=True)

    reviewer_faulty = WeeklyReviewer(
        docs_client=client_normal,
        runner=faulty_gateway,
        spec=REVIEW_SPEC,
        doc_mappings=doc_mappings_normal,
    )

    # 워커 실패 시 False 를 반환하고 아무것도 기록하지 않아야 한다 (창작 폴백 금지)
    assert reviewer_faulty.generate_weekly_review("review_doc", base_date) is False
    captured = capsys.readouterr()
    assert "review 워커 실패" in captured.err
    assert "review 워커 실패" in reviewer_faulty.last_error
    assert client_normal.read_text("review_doc") == ""


def test_weekly_reviewer_returns_false_on_empty_response():
    """빈 응답을 문서에 기록하지 않는다."""
    client = MockGoogleDocsClient({
        "theme_10": (
            "━━━━━━━━━━━━━━━━━━\n"
            "■ [t305] 2026-07-15 | 제목\n"
            "▶ 핵심 주장: 주장\n"
            "▶ 판단 논리: 논리\n"
            "▶ 💭 내 생각: 생각\n"
        ),
        "review_doc": "",
    })
    reviewer = WeeklyReviewer(client, MockWorkerRunner(response_text="   "),
                             REVIEW_SPEC, {"반도체사이클": "theme_10"})

    assert reviewer.generate_weekly_review("review_doc", datetime(2026, 7, 18)) is False
    assert client.read_text("review_doc") == ""
    assert "빈 응답" in reviewer.last_error



def _theme_record(date_str: str, ident: str, claim: str) -> str:
    return (
        "━━━━━━━━━━━━━━━━━━\n"
        f"■ {date_str} | 제목 {ident}\n"
        f"▶ 식별자: [{ident}]\n"
        "▶ 출처: 피터케이 채널\n"
        f"▶ 핵심 주장: {claim}\n"
        "▶ 근거·데이터:\n"
        "  - 근거 1\n"
        "▶ 판단 논리: 논리\n"
        "▶ 리스크·반대 시나리오:\n"
        "  - 리스크\n"
        "▶ 💭 내 생각: 메모\n"
        "▶ ❓ 생각해볼 질문: 질문?\n"
    )


def test_weekly_review_trims_whole_cards_oldest_first(capsys):
    """한도를 넘으면 글자 단위가 아니라 카드 단위로, 날짜가 오래된 것부터 뺀다.

    2026-10-19 W41 리뷰에서 글자 수로 자른 탓에 첫 카드의 날짜·제목이 잘려 나갔다.
    레코드는 문서별로 모이므로 문서 순서가 아니라 날짜 순서로 빼야 한다.
    """
    long_claim = "가" * 600
    # 최신 글이 앞 문서, 오래된 글이 뒤 문서에 있다 — 문서 순서로 자르면 최신 글이 빠진다
    doc_contents = {
        "theme_a": _theme_record("2026-07-18", "t1", long_claim)
                   + _theme_record("2026-07-17", "t2", long_claim),
        "theme_b": _theme_record("2026-07-12", "t3", long_claim)
                   + _theme_record("2026-07-13", "t4", long_claim),
    }
    client = MockGoogleDocsClient(doc_contents)
    gateway = MockWorkerRunner(response_text="리뷰 본문")
    spec = WorkerSpec(name="review", model="claude-opus-5-5-high", max_prompt_chars=2600)
    reviewer = WeeklyReviewer(
        docs_client=client,
        runner=gateway,
        spec=spec,
        doc_mappings={"반도체사이클": "theme_a", "금리": "theme_b"},
    )

    assert reviewer.generate_weekly_review("review_doc", datetime(2026, 7, 18, 9, 0)) is True

    prompt = gateway.prompts[0]
    assert len(prompt) <= spec.max_prompt_chars
    # 남은 카드는 머리(날짜·제목)부터 질문까지 온전하다
    kept = [i for i in ("t1", "t2", "t3", "t4") if f"제목: 제목 {i}\n" in prompt]
    assert 0 < len(kept) < 4
    assert prompt.count("- 날짜: ") == len(kept)
    assert prompt.count("❓ 질문: 질문?") == len(kept)
    # 빠진 것은 가장 오래된 카드부터다 — 최신(t1)은 남고 최고(t3)는 빠진다
    assert "t1" in kept
    assert "t3" not in kept
    # 남은 카드는 날짜순으로 놓인다
    dates = [line.split(": ")[1] for line in prompt.splitlines() if line.startswith("- 날짜: ")]
    assert dates == sorted(dates)
    assert "오래된" in capsys.readouterr().err


def test_weekly_review_skips_when_no_card_fits():
    client = MockGoogleDocsClient({"theme_a": _theme_record("2026-07-18", "t1", "가" * 2000)})
    gateway = MockWorkerRunner(response_text="리뷰 본문")
    spec = WorkerSpec(name="review", model="claude-opus-5-5-high", max_prompt_chars=1500)
    reviewer = WeeklyReviewer(
        docs_client=client,
        runner=gateway,
        spec=spec,
        doc_mappings={"반도체사이클": "theme_a"},
    )

    assert reviewer.generate_weekly_review("review_doc", datetime(2026, 7, 18, 9, 0)) is False
    assert gateway.calls == 0
    assert client.read_text("review_doc") == ""


def test_parser_reads_identifier_from_field_line_when_header_has_none():
    rec = WeeklyReviewParser().parse_recent_records(
        _theme_record("2026-07-18", "t9", "주장"), datetime(2026, 7, 18, 9, 0)
    )[0]
    assert rec.identifier == "t9"


def test_parser_keeps_empty_thought_empty_and_drops_placeholder():
    """빈 '내 생각' 줄이 다음 줄(질문)을 삼키지 않고, 미기록 placeholder 는 비운다."""
    base = _theme_record("2026-07-18", "t1", "주장")
    empty = base.replace("▶ 💭 내 생각: 메모\n", "▶ 💭 내 생각: \n")
    placeholder = base.replace("▶ 💭 내 생각: 메모\n", "▶ 💭 내 생각: 미기록 [r1993]\n")
    parser = WeeklyReviewParser()
    when = datetime(2026, 7, 18, 9, 0)

    for text in (empty, placeholder):
        rec = parser.parse_recent_records(text, when)[0]
        assert rec.my_thought == ""
        assert rec.critical_question == "질문?"
        # 리스크 구간도 빈 생각 줄과 무관하게 그대로다
        assert rec.risks == ["리스크"]

    assert parser.parse_recent_records(base, when)[0].my_thought == "메모"


def test_strip_markdown_removes_quote_and_callout_markers():
    """W43 리뷰 본문에 `> [!NOTE]` 가 그대로 남았다."""
    text = "\n".join([
        "> [!NOTE]",
        "> 이번 주는 표본이 적다.",
        ">",
        "> [!WARNING] 확정 아님",
        ">> 중첩 인용",
        "> **굵게**",
        "본문 > 부등호는 유지",
        "[!NOTE] 인용 밖 표기는 건드리지 않는다",
    ])
    assert strip_markdown(text).split("\n") == [
        "이번 주는 표본이 적다.",
        "",
        "확정 아님",
        "중첩 인용",
        "굵게",
        "본문 > 부등호는 유지",
        "[!NOTE] 인용 밖 표기는 건드리지 않는다",
    ]



def test_theme_failure_after_write_counts_as_success():
    """본문을 기록했으면 테마 실패로 False 를 내지 않는다 (다음 배치가 중복 기록한다)."""

    class ThemeFailingClient(MockGoogleDocsClient):
        def apply_sepia_theme(self, doc_id: str) -> bool:
            return False

    client = ThemeFailingClient({
        "theme_10": _theme_record("2026-07-15", "r305", "주장"),
        "review_doc": "",
    })
    reviewer = WeeklyReviewer(client, MockWorkerRunner(response_text="리뷰 본문"), REVIEW_SPEC, {"반도체사이클": "theme_10"})

    assert reviewer.generate_weekly_review("review_doc", datetime(2026, 7, 18)) is True
    assert client.read_text("review_doc").count("주간리뷰 성찰 보고서") == 1
    assert "세피아 테마" in reviewer.last_warning
    assert reviewer.last_error == ""


def test_append_failure_returns_false():
    class AppendFailingClient(MockGoogleDocsClient):
        def append_text(self, doc_id: str, text: str) -> bool:
            return False

    client = AppendFailingClient({"theme_10": _theme_record("2026-07-15", "r305", "주장")})
    reviewer = WeeklyReviewer(client, MockWorkerRunner(response_text="리뷰 본문"), REVIEW_SPEC, {"반도체사이클": "theme_10"})

    assert reviewer.generate_weekly_review("review_doc", datetime(2026, 7, 18)) is False
    assert "append 실패" in reviewer.last_error
