import pytest
from autonote.restore_docs import CHUNK_CHARS, DocRestorer

SEP = "━━━━━━━━━━━━━━━━━━"


def block(ident, claim="정상 주장", thought="미기록"):
    return (
        f"{SEP}\n"
        f"■ [{ident}] 2026-07-20 | 제목 {ident}\n"
        "▶ 출처: 피터케이 채널\n"
        f"▶ 핵심 주장: {claim}\n"
        "▶ 근거·데이터:\n  - 9명중 3명\n"
        "▶ 판단 논리: 사람이 쓴 논리\n"
        "▶ 리스크·반대 시나리오:\n  - 변동성\n"
        f"▶ 💭 내 생각: {thought}\n"
        "▶ ❓ 생각해볼 질문: 질문?\n"
    )


CLEAN = block("r700") + block("r701", thought="내가 적은 의견")
CONTAMINATED = block("r700", claim="금리 분야 전략적 판단을 도출함")


class FakeDocs:
    def __init__(self, docs, clear_ok=True, append_ok=True, trash_ok=True):
        self.docs = dict(docs)
        self.clear_ok = clear_ok
        self.append_ok = append_ok
        self.trash_ok = trash_ok
        self.cleared = []
        self.trashed = []
        self.themed = []

    def read_text(self, doc_id):
        return self.docs.get(doc_id, "")

    def clear_body(self, doc_id):
        if not self.clear_ok:
            return False
        self.cleared.append(doc_id)
        self.docs[doc_id] = ""
        return True

    def append_text(self, doc_id, text):
        if not self.append_ok:
            return False
        self.docs[doc_id] = self.docs.get(doc_id, "") + text
        return True

    def apply_sepia_theme(self, doc_id):
        self.themed.append(doc_id)
        return True

    def trash_file(self, file_id):
        if not self.trash_ok:
            return False
        self.trashed.append(file_id)
        return True


class FakeStateManager:
    def __init__(self, overrides):
        self.overrides = dict(overrides)
        self.removed = []

    def remove_doc_override(self, key):
        self.overrides.pop(key, None)
        self.removed.append(key)


def setup(clean=CLEAN, original="orig", **kwargs):
    docs = FakeDocs({original: CONTAMINATED, "regen": clean}, **kwargs)
    sm = FakeStateManager({"금리": "regen"})
    return docs, sm, DocRestorer(docs, sm)


# ------------------------------------------------------------------ 정상 경로

def test_restore_writes_regenerated_content_into_original():
    docs, sm, restorer = setup()

    res = restorer.restore("금리", "orig", "regen")

    assert res.ok is True
    assert docs.cleared == ["orig"]
    final = docs.read_text("orig")
    assert "r700" in final and "r701" in final
    # 창작 문구가 사라져야 한다
    assert "전략적 판단을 도출함" not in final
    # 사용자 생각이 보존되어야 한다
    assert "내가 적은 의견" in final


def test_regenerated_doc_is_trashed_and_override_removed():
    """되쓰기가 끝나면 문서는 하나만 남고 우회 매핑도 사라진다."""
    docs, sm, restorer = setup()

    res = restorer.restore("금리", "orig", "regen")

    assert res.trashed is True
    assert docs.trashed == ["regen"]
    assert sm.removed == ["금리"]
    assert "금리" not in sm.overrides


def test_theme_is_applied_to_original():
    docs, sm, restorer = setup()
    restorer.restore("금리", "orig", "regen")
    assert docs.themed == ["orig"]


def test_content_is_chunked_on_block_boundaries():
    """블록이 중간에서 잘리면 문서가 깨진다."""
    big = "".join(block(f"r{i}") for i in range(60))
    docs = FakeDocs({"orig": CONTAMINATED, "regen": big})
    restorer = DocRestorer(docs, FakeStateManager({"금리": "regen"}))

    restorer.restore("금리", "orig", "regen")

    final = docs.read_text("orig")
    # 블록 수가 보존되어야 한다
    assert final.count(SEP) == 60
    assert len(big) > CHUNK_CHARS  # 실제로 분할이 일어나는 크기


# ------------------------------------------------------------------ 안전장치

def test_contaminated_regenerated_source_is_refused():
    """재생성본 자체가 오염이면 원본을 건드리지 않는다."""
    docs, sm, restorer = setup(clean=CONTAMINATED)

    res = restorer.restore("금리", "orig", "regen")

    assert res.ok is False
    assert "창작 표지" in res.detail
    assert docs.cleared == []          # 원본을 비우지 않았다
    assert sm.removed == []


def test_empty_regenerated_source_is_refused():
    docs = FakeDocs({"orig": CONTAMINATED, "regen": "   "})
    restorer = DocRestorer(docs, FakeStateManager({"금리": "regen"}))

    res = restorer.restore("금리", "orig", "regen")

    assert res.ok is False
    assert docs.cleared == []


def test_clear_failure_keeps_regenerated_doc():
    docs, sm, restorer = setup(clear_ok=False)

    res = restorer.restore("금리", "orig", "regen")

    assert res.ok is False
    assert docs.trashed == []
    assert sm.removed == []           # 우회 매핑 유지 → 재생성본이 계속 현행


def test_append_failure_keeps_regenerated_doc():
    docs, sm, restorer = setup(append_ok=False)

    res = restorer.restore("금리", "orig", "regen")

    assert res.ok is False
    assert docs.trashed == []
    assert sm.removed == []


def test_trash_failure_still_completes_restore():
    """휴지통 이동 실패는 되쓰기 성공을 무르지 않는다 (수동 정리만 남는다)."""
    docs, sm, restorer = setup(trash_ok=False)

    res = restorer.restore("금리", "orig", "regen")

    assert res.ok is True
    assert res.trashed is False
    assert sm.removed == ["금리"]


def test_can_skip_trashing():
    docs, sm, restorer = setup()
    res = restorer.restore("금리", "orig", "regen", trash_regenerated=False)

    assert res.ok is True
    assert docs.trashed == []
    assert sm.removed == ["금리"]
