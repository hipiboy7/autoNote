from datetime import datetime, timezone
from google_docs_client import GoogleDocsClient
from telegram_client import TelegramMessage
from analyzer import ResearchReport

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
        date_str = self._format_date(msg.date)
        
        evidence_str = "\n".join([f"  - {ev.strip()}" for ev in report.evidence])
        risks_str = "\n".join([f"  - {rk.strip()}" for rk in report.risks])

        formatted_text = (
            "━━━━━━━━━━━━━━━━━━\n"
            f"■ [{prefix}{msg.id}] {date_str} | {title}\n"
            "▶ 출처: 피터케이 채널\n"
            f"▶ 핵심 주장: {report.main_claim.strip()}\n"
            f"▶ 근거·데이터:\n{evidence_str}\n"
            f"▶ 판단 논리: {report.logic.strip()}\n"
            f"▶ 리스크·반대 시나리오:\n{risks_str}\n"
            # 식별자를 붙여 문서 내에서 유일한 문단으로 만든다.
            # Google Docs replaceAllText 는 한 문단 단위로만 일치하므로, 식별자가 없으면
            # 여러 블록의 '미기록' 줄이 구별되지 않아 생각 병합을 정밀하게 할 수 없다.
            f"▶ 💭 내 생각: 미기록 [{prefix}{msg.id}]\n"
            f"▶ ❓ 생각해볼 질문: {report.critical_question.strip()}\n"
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
