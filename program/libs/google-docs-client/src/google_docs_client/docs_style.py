"""문서에 세피아/라이트 테마를 실제로 적용한다 (Docs REST API).

왜 MCP 가 아니라 REST 인가: MCP 의 `docs.formatText` 는 문자 스타일
(bold/italic/heading* 등)만 지원하고 **문서 배경색을 바꿀 수 없다**. 과거 구현은
`style: "sepia"` 를 보내고 예외를 삼킨 뒤 무조건 True 를 반환해서, 규칙이 지켜지지
않는데도 성공으로 보고했다 (실측: 이 경로로 만든 문서는 background 가 미설정).

배경색·글자색은 `documents.batchUpdate` 의 `updateDocumentStyle` /
`updateTextStyle` 로만 지정할 수 있다. 색상값은 AGENTS.md '색상 통일' 규칙을 따른다.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Optional

DOCS_API = "https://docs.googleapis.com/v1/documents"

# AGENTS.md 색상 통일 규칙: 세피아/라이트 배경 + 다크 차콜 글자
DEFAULT_BG = (0.99, 0.96, 0.89)      # #FDF9E3
DEFAULT_FG = (0.17, 0.21, 0.28)      # 다크 차콜


def _rgb_from_env(name: str, fallback: tuple) -> tuple:
    raw = os.getenv(name)
    if not raw:
        return fallback
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 3:
        print(f"[SepiaTheme] {name}={raw!r} 형식이 'r,g,b' 가 아니어서 기본값을 씁니다.",
              file=sys.stderr)
        return fallback
    try:
        values = tuple(min(1.0, max(0.0, float(p))) for p in parts)
    except ValueError:
        print(f"[SepiaTheme] {name}={raw!r} 를 숫자로 읽을 수 없어 기본값을 씁니다.",
              file=sys.stderr)
        return fallback
    return values


def _optional_color(rgb: tuple) -> dict:
    """Docs API 의 OptionalColor 표현: {"color": {"rgbColor": ...}}

    중첩 깊이가 필드마다 다르다. textStyle.foregroundColor 는 OptionalColor 이므로
    이 형태 그대로 쓰고, documentStyle.background.color 는 Background.color 가
    OptionalColor 라서 한 단계 더 감싼다. 섞으면 400 INVALID_ARGUMENT 가 난다.
    """
    return {"color": {"rgbColor": {"red": rgb[0], "green": rgb[1], "blue": rgb[2]}}}


class SepiaThemeApplier:
    def __init__(self, token_provider, timeout: int = 60):
        self.token_provider = token_provider
        self.timeout = timeout
        self.background = _rgb_from_env("DOC_THEME_BACKGROUND_RGB", DEFAULT_BG)
        self.foreground = _rgb_from_env("DOC_THEME_FOREGROUND_RGB", DEFAULT_FG)

    # ------------------------------------------------------------------ 내부

    def _request(self, url: str, token: str, payload: Optional[dict] = None) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        req.add_header("Authorization", f"Bearer {token}")
        if data:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}

    def _document_end_index(self, doc_id: str, token: str) -> int:
        """본문 마지막 인덱스를 구한다. 글자색을 본문 전체에 적용하기 위해 필요하다."""
        doc = self._request(
            f"{DOCS_API}/{doc_id}?fields=body.content.endIndex", token
        )
        content = (doc.get("body") or {}).get("content") or []
        if not content:
            return 0
        return int(content[-1].get("endIndex", 0) or 0)

    # ------------------------------------------------------------------ 공개 API

    def apply(self, doc_id: str) -> bool:
        """배경색과 글자색을 적용한다. 실제 성공 여부를 반환한다 (조용한 True 금지)."""
        try:
            token = self.token_provider.get_token()
        except Exception as e:
            print(f"[SepiaTheme] 토큰 확보 실패 — 테마를 적용하지 못했습니다: {e}",
                  file=sys.stderr)
            return False

        requests = [{
            "updateDocumentStyle": {
                # Background.color 가 OptionalColor 이므로 한 단계 더 감싼다
                "documentStyle": {
                    "background": {"color": _optional_color(self.background)}
                },
                "fields": "background",
            }
        }]

        try:
            end_index = self._document_end_index(doc_id, token)
        except Exception as e:
            print(f"[SepiaTheme] 문서 구조 조회 실패 ({doc_id}): {str(e)[:200]}",
                  file=sys.stderr)
            end_index = 0

        # 본문이 비어 있으면(endIndex<=2) 적용할 텍스트 범위가 없다. 배경만 칠한다.
        if end_index > 2:
            requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": 1, "endIndex": end_index - 1},
                    # foregroundColor 는 OptionalColor 자체다 (중첩 한 단계)
                    "textStyle": {"foregroundColor": _optional_color(self.foreground)},
                    "fields": "foregroundColor",
                }
            })

        try:
            self._request(
                f"{DOCS_API}/{doc_id}:batchUpdate", token, {"requests": requests}
            )
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="ignore")[:300]
            except Exception:
                pass
            print(
                f"[SepiaTheme] 테마 적용 실패 ({doc_id}): HTTP {e.code} {detail}",
                file=sys.stderr,
            )
            return False
        except Exception as e:
            print(f"[SepiaTheme] 테마 적용 실패 ({doc_id}): {str(e)[:200]}",
                  file=sys.stderr)
            return False

        print(f"[SepiaTheme] 세피아/라이트 테마 적용 완료: {doc_id}")
        return True

    def verify(self, doc_id: str) -> Optional[tuple]:
        """현재 배경색을 조회한다. 미설정이면 None."""
        try:
            token = self.token_provider.get_token()
            doc = self._request(
                f"{DOCS_API}/{doc_id}?fields=documentStyle.background", token
            )
        except Exception as e:
            print(f"[SepiaTheme] 배경색 조회 실패 ({doc_id}): {str(e)[:200]}",
                  file=sys.stderr)
            return None
        rgb = (
            ((doc.get("documentStyle") or {}).get("background") or {})
            .get("color", {})
            .get("color", {})
            .get("rgbColor")
        )
        if not rgb:
            return None
        return (rgb.get("red", 0.0), rgb.get("green", 0.0), rgb.get("blue", 0.0))
