import pytest
from datetime import datetime

from original_docs import (
    OriginalDocRegistry,
    OriginalDocResolutionError,
    parse_monthly_mapping,
)


class FakeStateManager:
    def __init__(self, original_docs=None):
        self.original_docs = dict(original_docs or {})
        self.registered = []

    def load_state(self):
        class _State:
            pass

        state = _State()
        state.original_docs = dict(self.original_docs)
        return state

    def register_original_doc(self, month, doc_id):
        self.original_docs[month] = doc_id
        self.registered.append((month, doc_id))


class FakeDocsClient:
    def __init__(self, create_result="new_doc_id"):
        self.create_result = create_result
        self.created = []
        self.sepia_applied = []

    def create_doc(self, title, content="", folder_id=""):
        self.created.append((title, folder_id))
        return self.create_result

    def apply_sepia_theme(self, doc_id):
        self.sepia_applied.append(doc_id)
        return True


def test_parse_monthly_mapping_parses_pairs_and_ignores_junk():
    raw = " 2026-07=abc123 , 2026-08=def456 ,, bogus, =nokey, 2026-09= "
    assert parse_monthly_mapping(raw) == {"2026-07": "abc123", "2026-08": "def456"}
    assert parse_monthly_mapping(None) == {}
    assert parse_monthly_mapping("") == {}


def test_resolve_prefers_configured_mapping():
    client = FakeDocsClient()
    state = FakeStateManager()
    registry = OriginalDocRegistry(client, state, {"2026-08": "configured_doc"})

    assert registry.resolve(datetime(2026, 8, 4)) == "configured_doc"
    assert client.created == []  # 새 문서를 만들지 않아야 한다


def test_resolve_uses_previously_registered_doc_from_state():
    client = FakeDocsClient()
    state = FakeStateManager({"2026-09": "state_doc"})
    registry = OriginalDocRegistry(client, state, {})

    assert registry.resolve(datetime(2026, 9, 1)) == "state_doc"
    assert client.created == []


def test_resolve_creates_and_registers_missing_month():
    """9월 메시지가 7월 문서에 조용히 기록되는 일이 없어야 한다."""
    client = FakeDocsClient(create_result="sep_doc")
    state = FakeStateManager()
    registry = OriginalDocRegistry(
        client, state, {"2026-07": "jul_doc"}, drive_folder_id="folder_1"
    )

    resolved = registry.resolve(datetime(2026, 9, 15))

    assert resolved == "sep_doc"
    assert resolved != "jul_doc"
    assert client.created == [("00_원문보관 2026-09", "folder_1")]
    assert client.sepia_applied == ["sep_doc"]  # 세피아 테마 통일 규칙
    assert state.registered == [("2026-09", "sep_doc")]


def test_resolve_raises_when_creation_fails_instead_of_misfiling():
    client = FakeDocsClient(create_result="")  # 생성 실패
    state = FakeStateManager()
    registry = OriginalDocRegistry(client, state, {"2026-07": "jul_doc"})

    with pytest.raises(OriginalDocResolutionError) as exc_info:
        registry.resolve(datetime(2026, 10, 2))

    assert "2026-10" in str(exc_info.value)
    assert state.registered == []


def test_resolve_creates_only_once_per_month():
    client = FakeDocsClient(create_result="oct_doc")
    state = FakeStateManager()
    registry = OriginalDocRegistry(client, state, {})

    first = registry.resolve(datetime(2026, 10, 1))
    second = registry.resolve(datetime(2026, 10, 28))

    assert first == second == "oct_doc"
    assert len(client.created) == 1
