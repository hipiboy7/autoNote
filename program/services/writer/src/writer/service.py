from datetime import datetime, timezone
from google_docs_client import GoogleDocsClient
from telegram_client import TelegramMessage
from analyzer import ResearchReport

BLOCK_SEPARATOR = "━━━━━━━━━━━━━━━━━━"
DEFAULT_SOURCE = "피터케이 채널"
UNRECORDED = "미기록"


def format_research_block(
    identifier: str,
    date_str: str,
    title: str,
    report: ResearchReport,
    thought: str = "",
    source: str = DEFAULT_SOURCE,
) -> str:
    """테마/종목 문서에 기록할 리서치 블록 텍스트를 만든다.

    기록 경로(배치)와 재생성 경로(문서 복구)가 동일한 서식을 쓰도록 함수로 분리한다.
    thought 가 비어 있으면 식별자가 붙은 미기록 placeholder 를 넣는다. 식별자를 붙이는
    이유는 Google Docs replaceAllText 가 문단 단위로만 일치하기 때문이다
    (식별자 없으면 여러 블록의 '미기록' 줄을 구별할 수 없다).
    """
    evidence_str = "\n".join(f"  - {ev.strip()}" for ev in report.evidence)
    risks_str = "\n".join(f"  - {rk.strip()}" for rk in report.risks)
    thought_line = thought.strip() or f"{UNRECORDED} [{identifier}]"

    return (
        f"{BLOCK_SEPARATOR}\n"
        f"■ [{identifier}] {date_str} | {title}\n"
        f"▶ 출처: {source}\n"
        f"▶ 핵심 주장: {report.main_claim.strip()}\n"
        f"▶ 근거·데이터:\n{evidence_str}\n"
        f"▶ 판단 논리: {report.logic.strip()}\n"
        f"▶ 리스크·반대 시나리오:\n{risks_str}\n"
        f"▶ 💭 내 생각: {thought_line}\n"
        f"▶ ❓ 생각해볼 질문: {report.critical_question.strip()}\n"
    )


class ResearchWriter:
    def __init__(self, docs_client: GoogleDocsClient):
        self.docs_client = docs_client

    def _format_date(self, dt: datetime) -> str:
        # datetime 객체를 KST 또는 UTC 문자열 YYYY-MM-DD 형태로 변환
        return dt.strftime("%Y-%m-%d")

    def write_original_backup(self, doc_id: str, msg: TelegramMessage, title: str) -> bool:
        """원문 보관소 문서에 텔레그램 메시지 원문을 포맷팅하여 추가한다."""
        date_str = self._format_date(msg.date)
        formatted_text = (
            "━━━━━━━━━━━━━━━━━━\n"
            f"■ [r{msg.id}] {date_str} | {title}\n"
            "▶ 출처: 피터케이 채널\n"
            f"{msg.text.strip()}\n"
        )
        return self.docs_client.append_text(doc_id, formatted_text)

    def write_research_report(
        self, 
        doc_id: str, 
        msg: TelegramMessage, 
        report: ResearchReport, 
        prefix: str, 
        title: str
    ) -> bool:
        """테마/종목 노트 문서에 LLM 분석 결과를 구조화하여 추가하고 세피아 테마를 적용한다."""
        formatted_text = format_research_block(
            identifier=f"{prefix}{msg.id}",
            date_str=self._format_date(msg.date),
            title=title,
            report=report,
        )

        # 1. 본문 작성
        append_ok = self.docs_client.append_text(doc_id, formatted_text)
        # 2. 세피아 배경색 테마 일괄 적용
        theme_ok = self.docs_client.apply_sepia_theme(doc_id)
        
        return append_ok and theme_ok

    def pre_fill_thought_note(
        self, 
        doc_id: str, 
        msg: TelegramMessage, 
        prefix: str, 
        title: str,
        target_doc_name: str = "원문보관",
        target_doc_id: str = ""
    ) -> bool:
        """99_생각노트 문서 맨 아래에 당일 수집된 식별자와 내 생각 입력란을 Pre-fill 한다."""
        date_str = self._format_date(msg.date)
        link_str = f" 🔗[{target_doc_name} 바로가기]" if target_doc_id else ""
        formatted_text = (
            "━━━━━━━━━━━━━━━━━━\n"
            f"■ {date_str} 리서치 생각노트 작성 창구\n"
            f"- [{prefix}{msg.id}]{link_str} | {title}\n"
            "  ▶ 💭 내 생각:\n"
        )
        return self.docs_client.append_text(doc_id, formatted_text)
