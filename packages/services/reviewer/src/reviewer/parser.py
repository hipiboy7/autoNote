import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from pydantic import BaseModel

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
            # 헤더에서 추출한 식별자 사용 (Writer가 ■ [r305] 형식으로 기록)
            identifier = header_match.group("ident") or ""

            main_claim_match = re.search(r"▶\s*핵심 주장:\s*([^\n]+)", block)
            main_claim = main_claim_match.group(1).strip() if main_claim_match else ""

            logic_match = re.search(r"▶\s*판단 논리:\s*([^\n]+)", block)
            logic = logic_match.group(1).strip() if logic_match else ""

            my_thought_match = re.search(r"▶\s*💭\s*내 생각:\s*([^\n]+)", block)
            my_thought = my_thought_match.group(1).strip() if my_thought_match else ""

            critical_question_match = re.search(r"▶\s*❓\s*생각해볼 질문:\s*([^\n]+)", block)
            critical_question = critical_question_match.group(1).strip() if critical_question_match else ""

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
