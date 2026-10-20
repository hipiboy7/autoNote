import re
import sys
from datetime import datetime

from agy_gateway import WorkerRunner, WorkerSpec, WorkerUnavailableError
from google_docs_client import GoogleDocsClient

from .parser import ParsedRecord, WeeklyReviewParser

# 마크다운 기호를 Docs 본문에 그대로 노출시키지 않는다 (AGENTS.md 서식 규칙).
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_MD_HR = re.compile(r"^\s*([-*_])\1{2,}\s*$")
# 인용(>, 중첩 >>)과 GitHub 식 콜아웃 표지([!NOTE] 등). W43 리뷰 본문에 그대로 남았다.
_MD_QUOTE = re.compile(r"^\s{0,3}(?:>\s?)+")
_MD_CALLOUT = re.compile(r"^\[![A-Za-z]+\][+-]?\s*")


def strip_markdown(text: str) -> str:
    """LLM 응답의 마크다운 기호를 제거해 Docs 에 평문으로 기록한다."""
    lines = []
    for line in text.split("\n"):
        quoted = _MD_QUOTE.match(line)
        if quoted:
            line = line[quoted.end():]
            # 콜아웃 표지만 있는 줄은 버리고, 뒤에 제목이 붙어 있으면 제목만 남긴다
            line, had_callout = _MD_CALLOUT.subn("", line)
            if had_callout and not line.strip():
                continue
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
        runner: WorkerRunner,
        spec: WorkerSpec,
        doc_mappings: dict[str, str],
    ):
        """주간리뷰는 판단 품질이 가장 중요하므로 로스터에서 최고 추론 워커를 받는다.

        모델은 worker_roster.DEFAULT_ROSTER["review"] 를 따른다 (2026-10-19 기준 claude-opus-5-5-high).
        """
        self.docs_client = docs_client
        self.runner = runner
        self.spec = spec
        self.doc_mappings = doc_mappings
        self.parser = WeeklyReviewParser()
        # 직전 generate_weekly_review 가 False 를 낸 이유. 배치가 실패 알림에 싣는다.
        self.last_error = ""

    def generate_weekly_review(self, review_doc_id: str, base_date: datetime) -> bool:
        """최근 7일 지식을 수집/파싱하여 주간리뷰를 생성하고 리뷰 구글 Docs에 오버라이트 기록한다."""
        self.last_error = ""
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
        # ParsedRecord 리스트를 프롬프트에 주입하기 좋게 텍스트 직렬화.
        # 레코드는 문서별로 모이므로 날짜순으로 정렬해야 '오래된 카드부터 빼기' 가 성립한다
        # (안정 정렬이라 같은 날짜 안에서는 문서 순서가 유지된다).
        recent_records.sort(key=lambda rec: rec.date_str)
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

        system_prompt = (
            "너는 전문적인 글로벌 매크로 및 주식 리서치 수석 애널리스트다. "
            "지난 1주일 동안 축적된 리서치 지식 카드와 사용자의 개인 의견(내 생각) 목록이 제공된다. "
            "분석 규칙을 철저히 엄수하여 주간 리서치 종합 성찰 보고서(Weekly Review)를 작성하라. "
            "모든 핵심 논리 뒤에는 반드시 근거가 된 원문 날짜를 대괄호로 명시해야 한다(예: [07-12]). "
            "전망을 확정적 사실처럼 쓰지 말고, 금융 상품 매수/매도 제안은 절대 포함하지 마라."
        )

        prompt_head = (
            f"{system_prompt}\n\n"
            "아래 취합된 최근 1주일간의 리서치 지식 카드를 바탕으로 주간 리뷰 보고서를 작성해라.\n\n"
            "[1주일간 축적된 지식 카드 목록]\n"
        )
        prompt_tail = (
            "\n\n"
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

        # 4. review 워커를 호출하여 주간 요약 텍스트 획득.
        #    실패 시 아무것도 기록하지 않는다 (창작 폴백 금지).
        #    지식 카드가 너무 많으면 오래된 카드부터 통째로 뺀다 (프로그램 책임).
        #    글자 수로 자르면 첫 카드의 날짜·제목이 잘려 나가 리뷰가 근거 날짜를
        #    인용할 수 없다 (2026-10-19 W41 리뷰에서 r1709 가 그렇게 잘렸다).
        kept = cards_text_list
        budget = self.spec.max_prompt_chars - len(prompt_head) - len(prompt_tail)
        while kept and len("\n".join(kept)) > budget:
            kept = kept[1:]
        if not kept:
            self.last_error = "지식 카드가 1건도 프롬프트 한계에 들어가지 않아 기록하지 않았습니다."
            print(f"[WeeklyReviewer] {self.last_error}", file=sys.stderr)
            return False
        dropped = len(cards_text_list) - len(kept)
        if dropped:
            print(
                f"[WeeklyReviewer] 지식 카드가 프롬프트 한계를 넘어 오래된 {dropped}건을 빼고 "
                f"최근 {len(kept)}건({recent_records[dropped].date_str}~)만 사용합니다.",
                file=sys.stderr,
            )
        prompt = prompt_head + "\n".join(kept) + prompt_tail

        try:
            result = self.runner.run(self.spec, prompt)
            review_content = (
                result.structured
                if isinstance(result.structured, str)
                else result.raw_response
            )
        except WorkerUnavailableError as e:
            self.last_error = f"review 워커 실패 ({self.spec.model}): {str(e)[:300]}"
            print(f"[WeeklyReviewer] {self.last_error}", file=sys.stderr)
            return False

        if not (review_content or "").strip():
            self.last_error = f"review 워커({self.spec.model})가 빈 응답을 반환해 기록하지 않았습니다."
            print(f"[WeeklyReviewer] {self.last_error}", file=sys.stderr)
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
        if not write_ok:
            self.last_error = "리뷰 문서에 본문을 기록하지 못했습니다 (Docs append 실패)."
        elif not theme_ok:
            self.last_error = "리뷰 본문은 기록했지만 세피아 테마 적용에 실패했습니다."

        return write_ok and theme_ok
