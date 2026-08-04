import re

class ThoughtNoteParser:
    def __init__(self):
        # [t305] 아래 줄의 "▶ 💭 내 생각: 내용"을 대소문자 무시하고 매칭하는 정규식
        self.comment_pattern = re.compile(
            r"\[([tsfr]\d+)\][^\n]*\n\s*▶\s*💭\s*내\s*생각:\s*([^\n]+)",
            re.IGNORECASE
        )

    def parse_comments(self, text: str) -> dict[str, str]:
        """생각노트 텍스트에서 유효한 코멘트들을 추출하여 dict[식별자, 코멘트] 구조로 반환한다."""
        comments = {}
        for match in self.comment_pattern.finditer(text):
            identifier = match.group(1).lower()  # 키는 소문자로 정규화
            comment_content = match.group(2).strip()
            
            # 건너뜀 조건: 비어있거나, '미기록'이거나, 시스템 서식문자(▶, - [, ━ 등)로 시작하는 경우
            if not comment_content or comment_content == "미기록" or comment_content.startswith("미기록"):
                continue
            if comment_content.startswith("▶") or comment_content.startswith("- [") or comment_content.startswith("━"):
                continue
                
            comments[identifier] = comment_content
            
        return comments
