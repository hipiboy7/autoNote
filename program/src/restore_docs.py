"""재생성 문서의 내용을 원본 문서로 되쓰고, 문서를 하나로 정리한다.

왜 필요한가: 복구(`--mode repair`)는 안전을 위해 **새 문서**를 만든다. 재생성이
잘못됐을 때 원본으로 되돌릴 수 있어야 하기 때문이다. 그러나 검증을 통과한 뒤에도
두 문서가 남으면 문서 수가 두 배가 되고, Ctrl+F 검색에 오염본이 함께 걸리고,
`doc_overrides` 라는 우회 장치가 영구적으로 남는다. 좋은 최종 상태가 아니다.

되쓰기 절차 (어느 시점에도 정상 내용 사본이 최소 하나 존재한다):
  1. 재생성 문서 본문을 읽는다 (사본 A)
  2. 원본 문서 본문을 비우고 사본 A 를 넣는다
  3. 원본을 재검증한다 (창작 표지 0, 블록 수 일치)
  4. 통과하면 재생성 임시 문서를 휴지통으로 보내고 `doc_overrides` 항목을 제거한다

3 에서 실패하면 아무것도 지우지 않고 보고한다. 그 경우 `doc_overrides` 가 남아
재생성본이 계속 현행으로 쓰이므로 사용자가 잃는 것은 없다.
"""

import sys
from dataclasses import dataclass
from typing import Optional

from analyzer.grounding import find_fabrication_markers
from writer import BLOCK_SEPARATOR

# Docs 요청 크기를 고려한 분할 기준
CHUNK_CHARS = 8000


@dataclass
class RestoreResult:
    name: str
    ok: bool = False
    blocks_before: int = 0
    blocks_after: int = 0
    markers_after: int = 0
    trashed: bool = False
    detail: str = ""


def _count_blocks(text: str) -> int:
    return len([c for c in text.split(BLOCK_SEPARATOR) if c.strip()])


class DocRestorer:
    def __init__(self, docs_client, state_manager, apply_theme: bool = True):
        self.docs_client = docs_client
        self.state_manager = state_manager
        self.apply_theme = apply_theme

    def restore(
        self,
        name: str,
        original_doc_id: str,
        regenerated_doc_id: str,
        trash_regenerated: bool = True,
    ) -> RestoreResult:
        result = RestoreResult(name=name)

        source = self.docs_client.read_text(regenerated_doc_id)
        if not source.strip():
            result.detail = "재생성 문서를 읽지 못했습니다. 아무것도 변경하지 않았습니다."
            print(f"[Restore] {name}: {result.detail}", file=sys.stderr)
            return result

        result.blocks_before = _count_blocks(source)

        # 재생성본 자체가 오염되어 있으면 되쓰면 안 된다
        markers = find_fabrication_markers([source])
        if markers:
            result.detail = f"재생성본에 창작 표지가 남아 있습니다: {markers}"
            print(f"[Restore] {name}: {result.detail}", file=sys.stderr)
            return result

        print(f"[Restore] {name}: 원본 본문을 비우고 재생성 내용 {len(source)}자를 넣습니다.")
        if not self.docs_client.clear_body(original_doc_id):
            result.detail = "원본 본문 비우기에 실패했습니다. 재생성본은 그대로 유지됩니다."
            print(f"[Restore] {name}: {result.detail}", file=sys.stderr)
            return result

        for chunk in self._chunks(source):
            if not self.docs_client.append_text(original_doc_id, chunk):
                result.detail = (
                    "원본에 내용을 쓰는 중 실패했습니다. 재생성본이 남아 있으므로 "
                    "doc_overrides 를 유지합니다."
                )
                print(f"[Restore] {name}: {result.detail}", file=sys.stderr)
                return result

        if self.apply_theme:
            self.docs_client.apply_sepia_theme(original_doc_id)

        # 되쓰기 검증
        after = self.docs_client.read_text(original_doc_id)
        result.blocks_after = _count_blocks(after)
        result.markers_after = len(find_fabrication_markers([after]))

        if result.markers_after or result.blocks_after < result.blocks_before:
            result.detail = (
                f"되쓰기 검증 실패 (블록 {result.blocks_before}→{result.blocks_after}, "
                f"창작표지 {result.markers_after}). 재생성본을 지우지 않습니다."
            )
            print(f"[Restore] {name}: {result.detail}", file=sys.stderr)
            return result

        result.ok = True

        if trash_regenerated:
            result.trashed = self.docs_client.trash_file(regenerated_doc_id)
            if not result.trashed:
                print(
                    f"[Restore] {name}: 임시 재생성 문서를 휴지통으로 옮기지 못했습니다. "
                    "수동으로 정리하십시오.",
                    file=sys.stderr,
                )

        # 원본이 현행이 되었으므로 우회 매핑을 제거한다
        self.state_manager.remove_doc_override(name)
        print(
            f"[Restore] {name}: 완료 — 원본 {original_doc_id} 이 현행입니다 "
            f"(블록 {result.blocks_after}, 창작표지 0). doc_overrides 항목 제거."
        )
        return result

    @staticmethod
    def _chunks(text: str):
        """블록 경계를 지켜 나눈다 (블록이 중간에서 잘리면 안 된다)."""
        parts = [p for p in text.split(BLOCK_SEPARATOR) if p.strip()]
        buffer = ""
        for part in parts:
            block = f"{BLOCK_SEPARATOR}\n{part.strip()}\n"
            if buffer and len(buffer) + len(block) > CHUNK_CHARS:
                yield buffer
                buffer = block
            else:
                buffer += block
        if buffer:
            yield buffer
