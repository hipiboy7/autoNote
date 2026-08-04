"""월별 원문보관 문서를 조회하고, 없으면 생성하여 상태 파일에 등록한다.

기존 구현은 `{"2026-07": ..., "2026-08": ...}` 를 코드에 하드코딩하고 미등록 월에는
7월 문서로 조용히 폴백했다. 9월부터 모든 원문이 7월 문서에 기록되면서도 에러가
발생하지 않는 구조였다. 여기서는 다음 순서로 해결한다.

1. 환경변수 `ORIGINAL_DOC_MONTHLY` 로 명시된 매핑
2. `state.json` 에 등록된, 과거 배치가 자동 생성한 문서
3. 신규 생성 후 `state.json` 에 등록

세 경로 모두 실패하면 예외를 던진다. 엉뚱한 달의 문서에 기록하는 것보다
배치를 멈추는 편이 안전하다.
"""

import sys
from datetime import datetime
from typing import Dict, Optional

from google_docs_client import GoogleDocsClient

DOC_TITLE_TEMPLATE = "00_원문보관 {month}"


def parse_monthly_mapping(raw: Optional[str]) -> Dict[str, str]:
    """"YYYY-MM=docId,YYYY-MM=docId" 형식의 문자열을 dict 로 파싱한다."""
    mapping: Dict[str, str] = {}
    if not raw:
        return mapping
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        month, _, doc_id = chunk.partition("=")
        month, doc_id = month.strip(), doc_id.strip()
        if month and doc_id:
            mapping[month] = doc_id
    return mapping


class OriginalDocResolutionError(RuntimeError):
    """월별 원문보관 문서를 확보하지 못했음을 알린다."""


class OriginalDocRegistry:
    def __init__(
        self,
        docs_client: GoogleDocsClient,
        state_manager,
        configured: Optional[Dict[str, str]] = None,
        drive_folder_id: str = "",
    ):
        self.docs_client = docs_client
        self.state_manager = state_manager
        self.configured = dict(configured or {})
        self.drive_folder_id = drive_folder_id
        self._cache: Dict[str, str] = {}

    def resolve(self, msg_date: datetime) -> str:
        month = msg_date.strftime("%Y-%m")

        if month in self._cache:
            return self._cache[month]

        # 1. 환경변수로 명시된 매핑
        doc_id = self.configured.get(month)

        # 2. 과거 배치가 자동 생성해 등록해 둔 문서
        if not doc_id:
            state = self.state_manager.load_state()
            doc_id = (getattr(state, "original_docs", None) or {}).get(month)

        # 3. 신규 생성
        if not doc_id:
            doc_id = self._create_monthly_doc(month)

        self._cache[month] = doc_id
        return doc_id

    def _create_monthly_doc(self, month: str) -> str:
        title = DOC_TITLE_TEMPLATE.format(month=month)
        print(f"[OriginalDocRegistry] {month} 원문보관 문서가 없어 새로 생성합니다: '{title}'")

        doc_id = self.docs_client.create_doc(title, folder_id=self.drive_folder_id)
        if not doc_id:
            raise OriginalDocResolutionError(
                f"{month} 원문보관 문서를 생성하지 못했습니다. "
                f"'{title}' 문서를 직접 만든 뒤 ORIGINAL_DOC_MONTHLY 에 "
                f"'{month}=<문서ID>' 를 추가하십시오. "
                "잘못된 달의 문서에 기록하지 않기 위해 배치를 중단합니다."
            )

        # 기존 문서들과 동일한 세피아/라이트 테마 유지 (AGENTS.md 문서 색상 통일 규칙)
        self.docs_client.apply_sepia_theme(doc_id)
        self.state_manager.register_original_doc(month, doc_id)
        print(
            f"[OriginalDocRegistry] {month} 원문보관 문서 등록 완료 (id={doc_id}). "
            "AGENTS.md 문서 매핑표에도 추가하십시오.",
            file=sys.stderr,
        )
        return doc_id
