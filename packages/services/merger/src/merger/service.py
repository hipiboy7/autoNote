import re
import sys
from typing import Optional

from google_docs_client import GoogleDocsClient

from .parser import ThoughtNoteParser

BLOCK_SEPARATOR = "━━━━━━━━━━━━━━━━━━"
# 병합 대상에서 제외할 매핑 키 (원문보관에는 💭 줄이 없고, 리뷰/생각노트는 본문이 아니다)
NON_TARGET_KEYS = ("원문보관", "thought", "review")


class CommentMerger:
    def __init__(self, docs_client: GoogleDocsClient, doc_mappings: dict[str, str]):
        self.docs_client = docs_client
        self.doc_mappings = doc_mappings
        self.parser = ThoughtNoteParser()

    def _target_doc_ids(self) -> list[str]:
        return list(dict.fromkeys(
            doc_id for key, doc_id in self.doc_mappings.items()
            if key not in NON_TARGET_KEYS
        ))

    def _find_placeholder(self, doc_text: str, ident: str) -> Optional[str]:
        """해당 식별자 블록의 '미기록' 문단 원문을 찾아 반환한다.

        치환 대상이 문서 내에서 유일하다고 보장되지 않으면 None 을 반환한다.
        엉뚱한 블록을 덮어쓰는 것보다 병합을 포기하고 보고하는 편이 안전하다.
        """
        # 1. 신규 형식: '▶ 💭 내 생각: 미기록 [r755]' — 식별자가 붙어 문단이 유일하다.
        unique_pattern = re.compile(
            rf"▶\s*💭\s*내\s*생각:\s*미기록\s*\[{re.escape(ident)}\]",
            re.IGNORECASE,
        )
        match = unique_pattern.search(doc_text)
        if match:
            candidate = match.group(0)
            if doc_text.count(candidate) == 1:
                return candidate

        # 2. 레거시 형식: 식별자 접미사가 없는 '미기록' 줄.
        #    식별자 헤더 이후 블록 내부에 있고, 문서 전체에서 유일할 때만 치환을 허용한다.
        header = re.search(rf"\[{re.escape(ident)}\]", doc_text, re.IGNORECASE)
        if not header:
            return None

        block_end = doc_text.find(BLOCK_SEPARATOR, header.end())
        block = doc_text[header.end(): block_end if block_end != -1 else len(doc_text)]

        legacy = re.search(r"▶\s*💭\s*내\s*생각:\s*미기록(?!\s*\[)", block)
        if not legacy:
            return None

        candidate = legacy.group(0)
        if doc_text.count(candidate) != 1:
            # 다른 블록의 '미기록' 줄과 구별할 수 없다
            return None
        return candidate

    def _merge_into_doc(self, doc_id: str, ident: str, comment: str) -> bool:
        doc_text = self.docs_client.read_text(doc_id)
        if not doc_text:
            return False

        if f"[{ident}]".lower() not in doc_text.lower():
            return False

        placeholder = self._find_placeholder(doc_text, ident)
        if not placeholder:
            print(
                f"[CommentMerger] [{ident}] 치환 대상을 특정할 수 없습니다 (doc={doc_id}). "
                "이미 생각이 기록되었거나 '미기록' 줄이 유일하지 않습니다.",
                file=sys.stderr,
            )
            return False

        return self.docs_client.replace_line(
            doc_id, placeholder, f"▶ 💭 내 생각: {comment}"
        )

    def merge_thoughts(self, thought_doc_id: str) -> list[str]:
        """생각노트의 의견을 본문 Docs 에 병합하고, **성공한 항목만** 처리완료 처리한다."""
        thought_text = self.docs_client.read_text(thought_doc_id)
        if not thought_text:
            return []

        comments = self.parser.parse_comments(thought_text)
        if not comments:
            return []

        merged_ids: list[str] = []
        failed: list[tuple[str, str]] = []

        for ident, comment in comments.items():
            merged = False
            for target_doc_id in self._target_doc_ids():
                if self._merge_into_doc(target_doc_id, ident, comment):
                    merged = True
                    break  # 식별자를 보유한 문서 한 곳에만 기록한다

            if merged:
                merged_ids.append(ident)
            else:
                failed.append((ident, comment))

        # 병합에 성공한 식별자만 생각노트에서 처리완료로 표시한다.
        # 실패한 항목을 처리완료로 남기면 사용자가 적은 생각이 반영도 되지 않은 채 사라진다.
        for ident in merged_ids:
            actual = re.search(
                rf"\[{re.escape(ident)}\]", thought_text, re.IGNORECASE
            )
            if not actual:
                continue
            token = actual.group(0)
            if not self.docs_client.replace_line(
                thought_doc_id, token, f"[{ident}_처리완료]"
            ):
                print(
                    f"[CommentMerger] [{ident}] 본문 병합은 성공했으나 생각노트 처리완료 "
                    "표시에 실패했습니다. 다음 배치에서 중복 병합될 수 있습니다.",
                    file=sys.stderr,
                )

        if failed:
            print(
                f"[CommentMerger] 병합 실패 {len(failed)}건 — 생각노트에 그대로 남겨 두었습니다. "
                "다음 배치에서 재시도되며, 아래 내용을 수동으로 붙여넣어도 됩니다.",
                file=sys.stderr,
            )
            for ident, comment in failed:
                print(f"  [{ident}] ▶ 💭 내 생각: {comment}", file=sys.stderr)

        return merged_ids
