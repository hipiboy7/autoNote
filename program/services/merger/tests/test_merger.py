import pytest
from google_docs_client import GoogleDocsClient
from merger.parser import ThoughtNoteParser
from merger.service import CommentMerger

class MockGoogleDocsClient(GoogleDocsClient):
    def __init__(self, doc_contents: dict[str, str] = None):
        self.doc_contents = doc_contents or {}
        self.overwrite_log = {}

    def append_text(self, doc_id: str, text: str) -> bool:
        return True

    def read_text(self, doc_id: str) -> str:
        return self.doc_contents.get(doc_id, "")

    def overwrite_text(self, doc_id: str, text: str) -> bool:
        self.doc_contents[doc_id] = text
        self.overwrite_log[doc_id] = text
        return True

    def apply_sepia_theme(self, doc_id: str) -> bool:
        return True

    def batch_update(self, doc_id: str, requests: list) -> bool:
        return True

def test_thought_note_parser_extracts_comments():
    thought_note = (
        "━━━━━━━━━━━━━━━━━━\n"
        "■ 2026-07-15 리서치 생각노트 작성 창구\n"
        "- [t305] 삼성전자 반도체 공급망 코멘트\n"
        "  ▶ 💭 내 생각: 반도체 고점 대비 20% 하락 시 적극 매수 고려 필요.\n"
        "- [S306] 한화에어로 방산 분석\n"
        "  ▶ 💭 내 생각: 지정학적 긴장 고조에 따른 수주 잔고 지속 증가 예상.\n"
        "- [f101] 환율 매크로 판단\n"
        "  ▶ 💭 내 생각: 미기록\n"  # 미기록 건너뜀 대상
        "- [r307] 잡담성 원문\n"
        "  ▶ 💭 내 생각: \n"          # 공백 건너뜀 대상
    )

    parser = ThoughtNoteParser()
    comments = parser.parse_comments(thought_note)

    # 대소문자 구분 없이 파싱되어야 함
    assert len(comments) == 2
    assert comments["t305"] == "반도체 고점 대비 20% 하락 시 적극 매수 고려 필요."
    assert comments["s306"] == "지정학적 긴장 고조에 따른 수주 잔고 지속 증가 예상."
    assert "f101" not in comments
    assert "r307" not in comments

def test_comment_merger_updates_main_docs():
    # 본문 문서 및 생각노트 초기 데이터 셋업
    thought_note_id = "thought_99"
    thought_note_content = (
        "━━━━━━━━━━━━━━━━━━\n"
        "■ 2026-07-15 리서치 생각노트 작성 창구\n"
        "- [t305] 삼성전자 HBM 실적 전망\n"
        "  ▶ 💭 내 생각: 단기적으로 퀄 통과 시 슈팅 기대되나 분할 매수로 대응.\n"
    )

    theme_doc_id = "theme_10"
    theme_doc_content = (
        "━━━━━━━━━━━━━━━━━━\n"
        "■ 2026-07-15 | 삼성전자 HBM 실적 전망\n"
        "▶ 식별자: [t305]\n"
        "▶ 핵심 주장: 퀄 승인 이후 이익 급증\n"
        "▶ 💭 내 생각: 미기록\n"
        "▶ ❓ 생각해볼 질문: 엔비디아 외 다변화 가능 여부?\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "■ 2026-07-14 | 다른 테마 글\n"
        "▶ 식별자: [t304]\n"
        "▶ 💭 내 생각: 미기록\n"
    )

    doc_contents = {
        thought_note_id: thought_note_content,
        theme_doc_id: theme_doc_content
    }
    doc_mappings = {
        "반도체사이클": theme_doc_id  # 반도체사이클 키로 theme_10 문서에 매핑됨
    }

    client = MockGoogleDocsClient(doc_contents)
    merger = CommentMerger(client, doc_mappings)

    # 병합 구동
    merged_ids = merger.merge_thoughts(thought_note_id)

    # 검증 1: t305가 성공적으로 병합 타겟으로 식별되었는지 확인
    assert merged_ids == ["t305"]

    # 검증 2: 본문 테마 Docs의 t305 내 생각 부분이 정밀하게 업데이트 되었는지 확인 (t304는 건드리지 않아야 함)
    updated_theme_content = client.read_text(theme_doc_id)
    assert "▶ 식별자: [t305]\n▶ 핵심 주장: 퀄 승인 이후 이익 급증\n▶ 💭 내 생각: 단기적으로 퀄 통과 시 슈팅 기대되나 분할 매수로 대응." in updated_theme_content
    assert "▶ 식별자: [t304]\n▶ 💭 내 생각: 미기록" in updated_theme_content # t304는 온전해야 함

    # 검증 3: 생각노트 내부가 처리완료 상태로 클린업 갱신되었는지 확인
    updated_thought_note = client.read_text(thought_note_id)
    assert "- [t305_처리완료]" in updated_thought_note

def test_comment_merger_handles_edge_cases(capsys):
    client = MockGoogleDocsClient({
        "empty_note": "",
        "no_valid_note": "- [t101] 의견\n  ▶ 💭 내 생각: 미기록\n",
        "unmapped_note": "- [t305] 매핑 안된 접두사\n  ▶ 💭 내 생각: 의견\n",
        "empty_target_note": "- [t305] 본문 비어있음\n  ▶ 💭 내 생각: 의견\n",
        "empty_target_doc": ""
    })
    
    # 't' 접두사에 대한 매핑이 없는 상태
    doc_mappings = {"한화에어로스페이스": "empty_target_doc"}
    merger = CommentMerger(client, doc_mappings)

    # 1. 생각노트 자체가 비어있는 경우
    assert merger.merge_thoughts("empty_note") == []

    # 2. 유효한 코멘트가 없는 경우
    assert merger.merge_thoughts("no_valid_note") == []

    # 3. 매핑되지 않은 접두사가 포함된 경우 — 해당 문서에서 식별자를 못 찾으므로 빈 결과 반환
    assert merger.merge_thoughts("unmapped_note") == []

    # 4. 대상 본문 Docs 내용이 비어있는 경우
    assert merger.merge_thoughts("empty_target_note") == []

