"""배치 이상을 사용자에게 즉시 알린다.

배경: 배치는 exit 0 으로 끝나면서 실패가 은폐될 수 있었고(창작 폴백 시기), 실패해도
사용자가 알 방법이 스케줄러 이력을 직접 뒤지는 것뿐이었다.

두 갈래의 '정상 동작 안 함' 을 모두 잡아야 한다.
  A. 배치가 돌았지만 실패했다  → main 이 예외를 잡아 이 모듈로 알린다.
  B. 배치가 아예 돌지 않았다    → 별도 워치독(cli/check_batch_health.py)이
     state.json 의 마지막 성공 시각을 보고 알린다. 배치 내부 알림으로는 잡을 수 없다.

전달 경로는 두 개를 병행한다.
  * 텔레그램 본인 저장 메시지 — 푸시라서 가장 빠르다 (휴대폰으로 즉시).
  * 99_생각노트 문서 배너 — 사용자가 매일 여는 문서라 놓치지 않는다.

**알림 실패가 배치를 깨뜨려서는 안 된다.** 모든 경로는 예외를 삼키고 성공 여부만 반환한다.
"""

import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Protocol

ALERT_HEADER = "🚨 autoNote 배치 이상"
OK_HEADER = "✅ autoNote 배치 정상 복구"
MAX_DETAIL_CHARS = 700


class SupportsSendMessage(Protocol):
    def send_message(self, chat_id: str, text: str, parse_mode: Optional[str] = None) -> bool:
        ...


class SupportsAppendText(Protocol):
    def append_text(self, doc_id: str, text: str) -> bool:
        ...


def _clip(text: str, limit: int = MAX_DETAIL_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… (이하 생략)"


@dataclass
class AlertContext:
    """알림 본문에 넣을 상황 정보."""

    mode: str = "batch"
    when: Optional[datetime] = None
    last_message_id: Optional[int] = None
    stopped_at_message_id: Optional[int] = None
    processed: Optional[int] = None
    hint: str = ""

    def lines(self) -> List[str]:
        out = [f"모드: {self.mode}"]
        if self.when:
            out.append(f"시각: {self.when.strftime('%Y-%m-%d %H:%M:%S')}")
        if self.stopped_at_message_id is not None:
            out.append(f"중단 지점: message {self.stopped_at_message_id}")
        if self.last_message_id is not None:
            out.append(f"마지막 처리 ID: {self.last_message_id}")
        if self.processed is not None:
            out.append(f"이번 실행 처리: {self.processed}건")
        return out


class BatchNotifier:
    def __init__(
        self,
        telegram_client: Optional[SupportsSendMessage] = None,
        docs_client: Optional[SupportsAppendText] = None,
        thought_doc_id: str = "",
        telegram_chat_id: str = "",
        enabled: bool = True,
    ):
        self.telegram_client = telegram_client
        self.docs_client = docs_client
        self.thought_doc_id = thought_doc_id
        # 기본값 'me' = 본인 저장 메시지. 다른 대화로 받으려면 NOTIFY_TELEGRAM_CHAT_ID 지정.
        self.telegram_chat_id = telegram_chat_id or os.getenv(
            "NOTIFY_TELEGRAM_CHAT_ID", "me"
        )
        self.enabled = enabled

    # ------------------------------------------------------------------ 공개 API

    def notify_failure(
        self,
        summary: str,
        detail: str = "",
        context: Optional[AlertContext] = None,
    ) -> bool:
        """실패를 알린다. 하나라도 전달되면 True."""
        body = self._build_failure_body(summary, detail, context)
        return self._dispatch(body, docs_body=self._build_docs_block(ALERT_HEADER, body))

    def notify_recovery(self, summary: str, context: Optional[AlertContext] = None) -> bool:
        """직전 실행이 실패였고 이번에 성공했을 때 복구를 알린다."""
        lines = [OK_HEADER, "", summary]
        if context:
            lines += ["", *context.lines()]
        body = "\n".join(lines)
        return self._dispatch(body, docs_body=self._build_docs_block(OK_HEADER, body))

    # -------------------------------------------------------------------- 내부

    def _build_failure_body(
        self, summary: str, detail: str, context: Optional[AlertContext]
    ) -> str:
        lines = [ALERT_HEADER, "", summary]
        if context:
            lines += ["", *context.lines()]
        if detail:
            lines += ["", "원인:", _clip(detail)]
        lines += [
            "",
            "조치: D:\\autoNote 에서 아래를 실행해 상태를 확인하십시오.",
            "  python -m autonote.main --mode status",
            "재실행: scripts\\run_daily_batch.bat",
        ]
        if context and context.hint:
            lines += ["", context.hint]
        return "\n".join(lines)

    @staticmethod
    def _build_docs_block(header: str, body: str) -> str:
        """생각노트 맨 아래에 붙일 배너 블록."""
        return (
            "━━━━━━━━━━━━━━━━━━\n"
            f"■ {header}\n"
            + "\n".join(f"▶ {line}" if line.strip() else "▶" for line in body.split("\n")[2:])
            + "\n"
        )

    def _dispatch(self, telegram_body: str, docs_body: str) -> bool:
        if not self.enabled:
            print("[BatchNotifier] 알림이 비활성화되어 전송하지 않습니다.", file=sys.stderr)
            return False

        delivered = False
        for label, fn in (
            ("telegram", lambda: self._send_telegram(telegram_body)),
            ("docs", lambda: self._append_docs(docs_body)),
        ):
            try:
                if fn():
                    delivered = True
                    print(f"[BatchNotifier] {label} 경로로 알림 전송 완료")
            except Exception as e:  # noqa: BLE001 - 알림이 배치를 깨서는 안 된다
                print(
                    f"[BatchNotifier] {label} 알림 실패: {str(e)[:200]}",
                    file=sys.stderr,
                )

        if not delivered:
            # 최후 수단: 터미널에 크게 남긴다
            print(
                "[BatchNotifier] 모든 알림 경로가 실패했습니다. 본문:\n" + telegram_body,
                file=sys.stderr,
            )
        return delivered

    def _send_telegram(self, body: str) -> bool:
        if not self.telegram_client:
            return False
        return bool(self.telegram_client.send_message(self.telegram_chat_id, body))

    def _append_docs(self, body: str) -> bool:
        if not (self.docs_client and self.thought_doc_id):
            return False
        return bool(self.docs_client.append_text(self.thought_doc_id, body))
