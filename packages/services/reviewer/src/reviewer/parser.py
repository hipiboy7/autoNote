import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from pydantic import BaseModel

# writer.UNRECORDED 와 같아야 한다. writer 가 생각이 비어 있는 블록에
# '미기록 [r305]' placeholder 를 넣는다 (reviewer 는 writer 에 의존하지 않는다).
_UNRECORDED = "미기록"

class ParsedRecord(BaseModel):
    date_str: str          # YYYY-MM-DD
    title: str             # 리서치 글 한 줄 제목
    identifier: str        # [t305] 등
    main_claim: str
    evidence: List[str]
    logic: str
    risks: List[str]
    my_thought: str
    critical_question: str

def _field(block: str, label: str) -> str:
    """'▶ <label>: 값' 한 줄의 값을 돌려준다. 값이 비어 있으면 빈 문자열.

    콜론 뒤 공백을 \\s* 로 받으면 줄바꿈까지 삼켜 값이 빈 줄일 때 다음 줄
    (예: '▶ ❓ 생각해볼 질문: ...')이 값으로 잡힌다. 같은 줄의 공백만 허용한다.
    """
    match = re.search(rf"▶[ \t]*{label}:[ \t]*([^\n]*)", block)
    return match.group(1).strip() if match else ""


class WeeklyReviewParser:
    def __init__(self):
        # 레코드 시작 형태 매칭: ■ [r305] 2026-07-15 | 제목 또는 ■ 2026-07-15 | 제목
        self.header_pattern = re.compile(
            r"■\s*(?:\[(?P<ident>[^\]]+)\]\s*)?(?P<date>\d{4}-\d{2}-\d{2})\s*\|\s*(?P<title>[^\n]+)"
        )

    def parse_recent_records(self, text: str, base_date: datetime) -> List[ParsedRecord]:
        """본문 텍스트를 파싱하여 최근 7일 내에 등록된 리서치 레코드들만 리스트로 반환한다."""
        records = []
        
        # 구분선 단위로 쪼갬
        blocks = text.split("━━━━━━━━━━━━━━━━━━")
        
        # 7일 범위 계산 (기준일 포함 이전 6일, 총 7일)
        start_date = (base_date - timedelta(days=6)).date()
        end_date = base_date.date()

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            header_match = self.header_pattern.search(block)
            if not header_match:
                continue

            date_str = header_match.group("date")
            title = header_match.group("title").strip()

            try:
                record_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            # 날짜 범위 체크 (7일 내)
            if not (start_date <= record_date <= end_date):
                continue

            # 세부 필드 파싱 (정규식 및 단순 텍스트 서치 활용)
            # 헤더에서 추출한 식별자 사용 (Writer가 ■ [r305] 형식으로 기록).
            # 헤더에 없으면 '▶ 식별자: [r305]' 줄을 본다.
            identifier = header_match.group("ident") or ""
            if not identifier:
                ident_line = _field(block, r"식별자")
                identifier = ident_line.strip("[]").strip()

            main_claim = _field(block, r"핵심 주장")
            logic = _field(block, r"판단 논리")

            # placeholder 는 사용자가 쓴 생각이 아니므로 비운다. 그래야 리뷰가
            # '💭 가 비어 있으면 기록 권장' 규칙을 따른다.
            my_thought = _field(block, r"💭\s*내 생각")
            if my_thought.startswith(_UNRECORDED):
                my_thought = ""

            critical_question = _field(block, r"❓\s*생각해볼 질문")

            # 근거·데이터 리스트 파싱
            evidence = []
            evidence_section = re.search(r"▶\s*근거·데이터:(.*?)▶\s*판단 논리:", block, re.DOTALL)
            if evidence_section:
                evidence_text = evidence_section.group(1)
                # '  - '로 시작하는 근거 라인들을 파싱
                for line in evidence_text.split("\n"):
                    cleaned_line = line.strip().lstrip("-").strip()
                    if cleaned_line:
                        evidence.append(cleaned_line)

            # 리스크 리스트 파싱
            risks = []
            risks_section = re.search(r"▶\s*리스크·반대 시나리오:(.*?)▶\s*💭", block, re.DOTALL)
            if risks_section:
                risks_text = risks_section.group(1)
                for line in risks_text.split("\n"):
                    cleaned_line = line.strip().lstrip("-").strip()
                    if cleaned_line:
                        risks.append(cleaned_line)

            records.append(ParsedRecord(
                date_str=date_str,
                title=title,
                identifier=identifier,
                main_claim=main_claim,
                evidence=evidence,
                logic=logic,
                risks=risks,
                my_thought=my_thought,
                critical_question=critical_question
            ))

        return records
