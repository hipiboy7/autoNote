import pytest
from datetime import datetime, timezone, timedelta
from google_docs_client import GoogleDocsClient
from agy_gateway import WorkerResult, WorkerRunner, WorkerSpec, WorkerUnavailableError
from reviewer.parser import WeeklyReviewParser, ParsedRecord
from reviewer.service import WeeklyReviewer

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

