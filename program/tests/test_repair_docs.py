import pytest
from analyzer import ResearchReport
from repair_docs import DocRepairer, parse_blocks, parse_original_sources

SEP = "━━━━━━━━━━━━━━━━━━"

# 과거 창작 폴백이 남긴 블록 (동일 문구가 여러 블록에 반복된다)
FABRICATED = (
    f"{SEP}\n"
    "■ [r700] 2026-07-20 | 금리 코멘트\n"
    "▶ 출처: 피터케이 채널\n"
    "▶ 핵심 주장: 금리 관련 저자 주요 결론 요약.\n"
    "▶ 근거·데이터:\n  - 본문 내 주요 수치 데이터 및 팩트 참고\n"
    "▶ 판단 논리: 저자는 지표를 바탕으로 금리 분야 전략적 판단을 도출함.\n"
    "▶ 리스크·반대 시나리오:\n  - 시장 변동성 및 거시경제 지표 변화에 따른 잠재적 위험 관찰 필요.\n"
    "▶ 💭 내 생각: 미기록 [r700]\n"
    "▶ ❓ 생각해볼 질문: 이 금리 가설이 유지되기 위해 추적해야 할 핵심 지표는 무엇인가?\n"
)

FABRICATED_WITH_THOUGHT = FABRICATED.replace(
    "■ [r700] 2026-07-20 | 금리 코멘트", "■ [r701] 2026-07-21 | 두 번째 코멘트"
).replace("▶ 💭 내 생각: 미기록 [r700]", "▶ 💭 내 생각: 내가 직접 적은 소중한 의견")

GENUINE = (
    f"{SEP}\n"
    "■ [r702] 2026-07-22 | 정상 블록\n"
    "▶ 출처: 피터케이 채널\n"
    "▶ 핵심 주장: 공급 부족이 지속된다\n"
    "▶ 근거·데이터:\n  - HBM3E 12단 통과\n"
    "▶ 판단 논리: 실제 사람이 쓴 논리\n"
    "▶ 리스크·반대 시나리오:\n  - 중국 수요 둔화\n"
    "▶ 💭 내 생각: 보유 유지\n"
    "▶ ❓ 생각해볼 질문: 판가 추이?\n"
)

ORIGINAL_ARCHIVE = (
    f"{SEP}\n"
    "■ [r700] 2026-07-20 | 금리 코멘트\n"
    "▶ 출처: 피터케이 채널\n"
    "fomc 회의에서 9명중 3명이 금리인상 의견을 냈다.\n"
    "물가가 안 잡히면 기계적으로 올린다는 의미.\n"
    f"{SEP}\n"
    "■ [r701] 2026-07-21 | 두 번째 코멘트\n"
    "▶ 출처: 피터케이 채널\n"
    "코스피는 고점대비 40% 빠졌다.\n"
)


class FakeDocsClient:
    def __init__(self, docs):
        self.docs = dict(docs)
        self.created = []
        self.sepia = []

    def read_text(self, doc_id):
        return self.docs.get(doc_id, "")

    def append_text(self, doc_id, text):
        self.docs[doc_id] = self.docs.get(doc_id, "") + text
        return True

    def overwrite_text(self, doc_id, text):
        self.docs[doc_id] = text
        return True

    def replace_line(self, doc_id, find_text, replace_text):
        return False

    def create_doc(self, title, content="", folder_id=""):
        doc_id = f"new::{title}"
        self.docs[doc_id] = content
        self.created.append((title, folder_id))
        return doc_id

    def apply_sepia_theme(self, doc_id):
        self.sepia.append(doc_id)
        return True

    def batch_update(self, doc_id, requests):
        return True


class FakeAnalyzer:
    def __init__(self, by_id=None, fail_ids=()):
        self.by_id = by_id or {}
        self.fail_ids = set(fail_ids)
        self.requested = []

    def analyze_batch(self, items):
        self.requested.extend(mid for mid, _ in items)
        out = {}
        for mid, _source in items:
            if mid in self.fail_ids:
                continue
            out[mid] = self.by_id.get(mid) or ResearchReport(
                main_claim=f"재생성된 주장 {mid}",
                evidence=["9명중 3명이 금리인상 의견"],
                logic="재생성된 논리",
                risks=["재생성된 리스크"],
                critical_question="재생성된 질문",
                theme="금리",
            )
        return out


# ----------------------------------------------------------------- 파싱

def test_parse_blocks_detects_fabrication_and_thoughts():
    blocks = parse_blocks(FABRICATED + FABRICATED_WITH_THOUGHT + GENUINE)

    assert [b.identifier for b in blocks] == ["r700", "r701", "r702"]
    assert [b.fabricated for b in blocks] == [True, True, False]
    # 미기록 placeholder 는 사용자 생각으로 보지 않는다
    assert blocks[0].thought == ""
    assert blocks[1].thought == "내가 직접 적은 소중한 의견"
    assert blocks[2].thought == "보유 유지"


def test_parse_original_sources_strips_header_and_source_line():
    sources = parse_original_sources([ORIGINAL_ARCHIVE])

    assert set(sources) == {"r700", "r701"}
    assert sources["r700"].startswith("fomc 회의에서")
    assert "출처" not in sources["r700"]
    assert "40%" in sources["r701"]


def test_parse_blocks_keeps_headerless_preamble():
    blocks = parse_blocks("문서 머리말입니다\n" + GENUINE)
    assert blocks[0].identifier == ""
    assert "문서 머리말" in blocks[0].text


# ----------------------------------------------------------------- 복구

def test_dry_run_counts_without_creating_document():
    client = FakeDocsClient({"theme": FABRICATED + GENUINE})
    repairer = DocRepairer(client, FakeAnalyzer())

    res = repairer.repair("금리", "theme", ["orig"], dry_run=True)

    assert res.total_blocks == 2
    assert res.fabricated == 1
    assert res.new_doc_id == ""
    assert client.created == []


def test_repair_rebuilds_into_new_doc_preserving_order_and_thoughts():
    client = FakeDocsClient({
        "theme": FABRICATED + FABRICATED_WITH_THOUGHT + GENUINE,
        "orig": ORIGINAL_ARCHIVE,
    })
    analyzer = FakeAnalyzer()
    repairer = DocRepairer(client, analyzer, drive_folder_id="folder_1")

    res = repairer.repair("금리", "theme", ["orig"])

    assert res.fabricated == 2
    assert res.repaired == 2
    assert res.new_doc_id.startswith("new::금리")
    assert client.created == [("금리 (재생성)", "folder_1")]
    assert client.sepia == [res.new_doc_id]

    rebuilt = client.read_text(res.new_doc_id)
    # 1. 창작 문구가 사라져야 한다
    assert "분야 전략적 판단을 도출함" not in rebuilt
    assert "관련 저자 주요 결론 요약" not in rebuilt
    # 2. 재생성 내용이 들어가야 한다
    assert "재생성된 주장 700" in rebuilt
    # 3. 사용자 생각은 오염 블록이라도 보존되어야 한다
    assert "내가 직접 적은 소중한 의견" in rebuilt
    # 4. 정상 블록은 원문 그대로 보존
    assert "실제 사람이 쓴 논리" in rebuilt
    # 5. 순서 유지
    assert rebuilt.index("r700") < rebuilt.index("r701") < rebuilt.index("r702")


def test_only_fabricated_blocks_are_reanalyzed():
    """정상 블록을 재분석하면 토큰 낭비이고 내용이 바뀔 위험도 있다."""
    client = FakeDocsClient({
        "theme": FABRICATED + GENUINE,
        "orig": ORIGINAL_ARCHIVE,
    })
    analyzer = FakeAnalyzer()

    DocRepairer(client, analyzer).repair("금리", "theme", ["orig"])

    assert analyzer.requested == [700]


def test_block_without_original_is_preserved_and_reported():
    """원문보관에 원문이 없으면 자동 복구 불가 — 기존 내용을 보존하고 보고한다."""
    orphan = FABRICATED.replace("[r700]", "[r999]")
    client = FakeDocsClient({"theme": orphan, "orig": ORIGINAL_ARCHIVE})
    analyzer = FakeAnalyzer()

    res = DocRepairer(client, analyzer).repair("금리", "theme", ["orig"])

    assert res.unrepairable == ["r999"]
    assert res.repaired == 0
    assert analyzer.requested == []
    # 기존 블록은 새 문서에 그대로 남아 있어야 한다 (유실 금지)
    assert "r999" in client.read_text(res.new_doc_id)


def test_failed_reanalysis_preserves_original_block():
    client = FakeDocsClient({
        "theme": FABRICATED,
        "orig": ORIGINAL_ARCHIVE,
    })
    analyzer = FakeAnalyzer(fail_ids={700})

    res = DocRepairer(client, analyzer).repair("금리", "theme", ["orig"])

    assert res.repaired == 0
    assert "r700" in res.unrepairable
    assert "r700" in client.read_text(res.new_doc_id)


def test_missing_document_raises():
    client = FakeDocsClient({})
    with pytest.raises(RuntimeError):
        DocRepairer(client, FakeAnalyzer()).repair("금리", "nope", ["orig"])
