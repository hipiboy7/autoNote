import re
import sys
from datetime import datetime
from google_docs_client import GoogleDocsClient
from llm_gateway import LLMGateway
from .parser import WeeklyReviewParser, ParsedRecord

# 마크다운 기호를 Docs 본문에 그대로 노출시키지 않는다 (AGENTS.md 서식 규칙).
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_MD_HR = re.compile(r"^\s*([-*_])\1{2,}\s*$")


def strip_markdown(text: str) -> str:
    """LLM 응답의 마크다운 기호를 제거해 Docs 에 평문으로 기록한다."""
    lines = []
    for line in text.split("\n"):
        if _MD_HR.match(line):
            continue
        line = _MD_HEADING.sub("", line)
        line = _MD_BOLD.sub(r"\1", line)
        line = _MD_ITALIC.sub(r"\1", line)
        lines.append(line.rstrip())
    return "\n".join(lines)

class WeeklyReviewer:
    def __init__(
        self, 
        docs_client: GoogleDocsClient, 
        llm_gateway: LLMGateway, 
        doc_mappings: dict[str, str],
        model_name: str,
        base_url: str,
        api_key: str
    ):
        self.docs_client = docs_client
        self.llm_gateway = llm_gateway
        self.doc_mappings = doc_mappings
        self.model_name = model_name
        self.base_url = base_url
        self.api_key = api_key
        self.parser = WeeklyReviewParser()

    def generate_weekly_review(self, review_doc_id: str, base_date: datetime) -> bool:
        """최근 7일 지식을 수집/파싱하여 주간리뷰를 생성하고 리뷰 구글 Docs에 오버라이트 기록한다."""
        recent_records = []

        # 1. 맵에 지정된 모든 테마/종목 Docs에서 7일 내 데이터 취합
        for prefix, doc_id in self.doc_mappings.items():
            # 원문보관(r)이나 리뷰(40) 문서는 수집 데이터셋에서 제외
            if prefix in ("원문보관", "review", "thought"):
                continue

            doc_text = self.docs_client.read_text(doc_id)
            if not doc_text:
                continue

            records = self.parser.parse_recent_records(doc_text, base_date)
            recent_records.extend(records)

        # 2. 신규 수집 내역이 0건인 경우 스킵 대체 텍스트 기록
        if not recent_records:
            skip_text = (
                "━━━━━━━━━━━━━━━━━━\n"
                f"■ {base_date.strftime('%Y-%m-%d')} 주간리뷰 성찰 보고서\n"
                "▶ 안내: 이번 주 수집된 신규 리서치가 없어 주간리뷰를 생성하지 않습니다.\n"
            )
            self.docs_client.append_text(review_doc_id, skip_text)
            self.docs_client.apply_sepia_theme(review_doc_id)
            return True

        # 3. 데이터가 존재하는 경우 AI 요약서 기안 조립
        # ParsedRecord 리스트를 프롬프트에 주입하기 좋게 텍스트 직렬화
        cards_text_list = []
        for rec in recent_records:
            card = (
                f"- 날짜: {rec.date_str}\n"
                f"  제목: {rec.title}\n"
                f"  식별자: {rec.identifier}\n"
                f"  핵심 주장: {rec.main_claim}\n"
                f"  판단 논리: {rec.logic}\n"
                f"  리스크: {', '.join(rec.risks)}\n"
                f"  💭 내 생각: {rec.my_thought}\n"
                f"  ❓ 질문: {rec.critical_question}\n"
            )
            cards_text_list.append(card)

        knowledge_cards_text = "\n".join(cards_text_list)

        system_prompt = (
            "너는 전문적인 글로벌 매크로 및 주식 리서치 수석 애널리스트다. "
            "지난 1주일 동안 축적된 리서치 지식 카드와 사용자의 개인 의견(내 생각) 목록이 제공된다. "
            "분석 규칙을 철저히 엄수하여 주간 리서치 종합 성찰 보고서(Weekly Review)를 작성하라. "
            "모든 핵심 논리 뒤에는 반드시 근거가 된 원문 날짜를 대괄호로 명시해야 한다(예: [07-12]). "
            "전망을 확정적 사실처럼 쓰지 말고, 금융 상품 매수/매도 제안은 절대 포함하지 마라."
        )

        user_prompt = (
            "아래 취합된 최근 1주일간의 리서치 지식 카드를 바탕으로 주간 리뷰 보고서를 작성해라.\n\n"
            f"[1주일간 축적된 지식 카드 목록]\n{knowledge_cards_text}\n\n"
            "[보고서 작성 필수 항목 및 서식]\n"
            "1. 이번 주 핵심 논리 3가지:\n"
            "   (저자는 [근거]를 보고 [논리]로 [판단]했다 한 문장 요약 + 상세 부연)\n"
            "2. 과거 기록 대비 저자의 관점 변화:\n"
            "   ([이전 관점] ➔ [현재 관점] 형식, 변화 없으면 '변화 없음' 명시)\n"
            "3. 사용자 생각(💭) 점검 및 대조:\n"
            "   (사용자가 적은 💭 의견을 분석하여 일치/불일치/의문 대조 성찰. 💭가 비어있다면 기록 권장)\n"
            "4. Next Action / 다음 주 관찰 포인트 3가지:\n"
            "   (검증해야 할 지표나 핵심 이벤트 3가지 도출)"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        # 4. reasoning-llm을 호출하여 주간 요약 텍스트 획득
        try:
            review_content = self.llm_gateway.request_chat(
                base_url=self.base_url,
                api_key=self.api_key,
                model=self.model_name,
                messages=messages,
                temperature=0.2
            )
        except Exception as e:
            print(f"[WeeklyReviewer] LLM generation failed: {e}", file=sys.stderr)
            return False

        # 5. 단일 통합 문서 맨 아래에 누적(append) 기록 후 세피아 배경 테마 인가.
        #    덮어쓰면 지난주 리뷰가 사라져 '관점 변화 추적'이라는 주간리뷰의 목적이 무너진다
        #    (AGENTS.md: 40_리뷰 는 누적 관리).
        review_block = (
            "━━━━━━━━━━━━━━━━━━\n"
            f"■ {base_date.strftime('%Y-%m-%d')} 주간리뷰 성찰 보고서"
            f" ({base_date.strftime('%G-W%V')})\n"
            f"{strip_markdown(review_content).strip()}\n"
        )
        write_ok = self.docs_client.append_text(review_doc_id, review_block)
        theme_ok = self.docs_client.apply_sepia_theme(review_doc_id)

        return write_ok and theme_ok
