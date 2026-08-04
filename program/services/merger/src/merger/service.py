import re
import sys
from google_docs_client import GoogleDocsClient
from .parser import ThoughtNoteParser

class CommentMerger:
    def __init__(self, docs_client: GoogleDocsClient, doc_mappings: dict[str, str]):
        self.docs_client = docs_client
        self.doc_mappings = doc_mappings
        self.parser = ThoughtNoteParser()

    def merge_thoughts(self, thought_doc_id: str) -> list[str]:
        """생각노트에서 의견 코멘트를 읽고, 본문 Docs에 정밀 병합한 뒤 완료 처리한다."""
        thought_text = self.docs_client.read_text(thought_doc_id)
        if not thought_text:
            return []

        # 1. 생각노트 내의 유효 코멘트들 추출
        comments = self.parser.parse_comments(thought_text)
        if not comments:
            return []

        merged_ids = []
        updated_thought_text = thought_text

        # 2. 개별 코멘트에 대한 병합 실행
        for ident, comment in comments.items():
            # 원문보관은 ▶ 💭 라인이 없으므로 제외. 테마/종목 문서만 순회하여 식별자를 찾는다.
            target_doc_ids = list(dict.fromkeys(
                doc_id for k, doc_id in self.doc_mappings.items() 
                if k not in ["원문보관", "thought", "review"]
            ))
            
            merged_success = False
            for target_doc_id in target_doc_ids:
                target_text = self.docs_client.read_text(target_doc_id)
                if not target_text:
                    continue

                ident_pattern = f"[{ident.lower()}]"
                ident_pattern_alt = f"[{ident.upper()}]"

                if ident_pattern not in target_text.lower():
                    continue

                # 3. 본문 내에서 해당 식별자 블록의 '내 생각: 미기록' 부분을 정밀 치환
                lines = target_text.split("\n")
                target_found = False
                updated_lines = []

                for line in lines:
                    if ident_pattern in line.lower() or ident_pattern_alt in line.upper():
                        target_found = True

                    if target_found and "▶ 💭 내 생각:" in line:
                        line = f"▶ 💭 내 생각: {comment}"
                        target_found = False

                    if target_found and "━━━━━━━━━━━━━━━━━━" in line:
                        target_found = False

                    updated_lines.append(line)

                # 4. 변경된 본문 텍스트 덮어쓰기
                overwrite_ok = self.docs_client.overwrite_text(target_doc_id, "\n".join(updated_lines))
                if overwrite_ok:
                    merged_success = True

            if merged_success:
                merged_ids.append(ident)
                # 5. 생각노트 내 해당 식별자를 [r305_처리완료] 형태로 변경 (대소문자 둘 다 대응)
                updated_thought_text = re.sub(
                    rf"\[({ident})\]", 
                    rf"[\1_처리완료]", 
                    updated_thought_text, 
                    flags=re.IGNORECASE
                )

        # 6. 최종 갱신된 생각노트 내용을 덮어씀
        if merged_ids:
            self.docs_client.overwrite_text(thought_doc_id, updated_thought_text)

        return merged_ids
