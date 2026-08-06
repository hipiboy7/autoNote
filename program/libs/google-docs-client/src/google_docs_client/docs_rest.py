"""Google Docs REST API 저수준 클라이언트.

MCP 도구로 할 수 없는 작업을 담당한다.
  * 문서 배경색·글자색 지정 (`updateDocumentStyle` / `updateTextStyle`)
  * 본문 전체 비우기 (`deleteContentRange`)

MCP 서버는 텍스트 추가/조회/치환만 노출하므로 이 둘은 REST 로만 가능하다.
"""

import json
import sys
import urllib.error
import urllib.request
from typing import Optional

DOCS_API = "https://docs.googleapis.com/v1/documents"


def http_error_detail(error: urllib.error.HTTPError) -> str:
    try:
        return error.read().decode("utf-8", errors="ignore")[:300]
    except Exception:
        return ""


class DocsRestClient:
    def __init__(self, token_provider, timeout: int = 60):
        self.token_provider = token_provider
        self.timeout = timeout

    # ------------------------------------------------------------------ 내부

    def request(self, url: str, payload: Optional[dict] = None) -> dict:
        token = self.token_provider.get_token()
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        req.add_header("Authorization", f"Bearer {token}")
        if data:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}

    # ------------------------------------------------------------------ 공개 API

    def body_end_index(self, doc_id: str) -> int:
        """본문 마지막 인덱스. 문서가 비어 있으면 1 에 가까운 값이 나온다."""
        doc = self.request(f"{DOCS_API}/{doc_id}?fields=body.content.endIndex")
        content = (doc.get("body") or {}).get("content") or []
        if not content:
            return 0
        return int(content[-1].get("endIndex", 0) or 0)

    def background_rgb(self, doc_id: str) -> Optional[tuple]:
        doc = self.request(f"{DOCS_API}/{doc_id}?fields=documentStyle.background")
        rgb = (
            ((doc.get("documentStyle") or {}).get("background") or {})
            .get("color", {})
            .get("color", {})
            .get("rgbColor")
        )
        if not rgb:
            return None
        return (rgb.get("red", 0.0), rgb.get("green", 0.0), rgb.get("blue", 0.0))

    def batch_update(self, doc_id: str, requests: list, label: str = "batchUpdate") -> bool:
        if not requests:
            return True
        try:
            self.request(f"{DOCS_API}/{doc_id}:batchUpdate", {"requests": requests})
        except urllib.error.HTTPError as e:
            print(
                f"[DocsRest] {label} 실패 ({doc_id}): HTTP {e.code} {http_error_detail(e)}",
                file=sys.stderr,
            )
            return False
        except Exception as e:
            print(f"[DocsRest] {label} 실패 ({doc_id}): {str(e)[:200]}", file=sys.stderr)
            return False
        return True

    def clear_body(self, doc_id: str) -> bool:
        """본문을 비운다.

        Docs 는 본문 마지막 개행을 지울 수 없으므로 [1, endIndex-1) 만 삭제한다.
        본문이 이미 비어 있으면(endIndex<=2) 아무것도 하지 않고 성공으로 본다.
        """
        try:
            end_index = self.body_end_index(doc_id)
        except Exception as e:
            print(f"[DocsRest] 본문 길이 조회 실패 ({doc_id}): {str(e)[:200]}",
                  file=sys.stderr)
            return False

        if end_index <= 2:
            return True

        return self.batch_update(
            doc_id,
            [{
                "deleteContentRange": {
                    "range": {"startIndex": 1, "endIndex": end_index - 1}
                }
            }],
            label="clear_body",
        )
