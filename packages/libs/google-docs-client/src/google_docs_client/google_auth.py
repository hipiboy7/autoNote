"""Google API 액세스 토큰을 확보한다.

MCP 서버(google-workspace 플러그인)가 관리하는 암호화 토큰 저장소를 읽어 쓴다.
저장소의 액세스 토큰은 약 1시간마다 만료되고, 만료된 토큰으로 REST 를 호출하면 401 이
난다. MCP 서버의 `auth.refreshToken` 도구는 갱신 결과를 **저장소에 다시 기록**하므로,
만료가 임박하면 그 도구를 호출한 뒤 다시 읽으면 된다.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

# 저장소 루트를 이 파일 위치에서 역산한다.
#   .../packages/libs/google-docs-client/src/google_docs_client/google_auth.py
#    parents[5] --------------------------------------------------- 저장소 루트
# 절대 경로를 박아 두면 디렉토리를 옮길 때 조용히 깨진다.
# GOOGLE_TOKEN_DECRYPT_SCRIPT 로 언제든 덮어쓸 수 있다.
_REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_DECRYPT_SCRIPT = str(_REPO_ROOT / "ops" / "tools" / "decrypt_token.js")
DEFAULT_ACCOUNT = "main-account"
# 만료 직전 토큰으로 호출하면 요청 도중 만료될 수 있어 여유를 둔다
DEFAULT_SKEW_SECONDS = 120


class GoogleAuthError(RuntimeError):
    """액세스 토큰을 확보하지 못했음을 알린다."""


class GoogleTokenProvider:
    def __init__(
        self,
        decrypt_script: Optional[str] = None,
        account: str = DEFAULT_ACCOUNT,
        refresh_callback: Optional[Callable[[], None]] = None,
        skew_seconds: int = DEFAULT_SKEW_SECONDS,
    ):
        self.decrypt_script = decrypt_script or os.getenv(
            "GOOGLE_TOKEN_DECRYPT_SCRIPT", DEFAULT_DECRYPT_SCRIPT
        )
        self.account = account
        self.refresh_callback = refresh_callback
        self.skew_seconds = skew_seconds

    # ------------------------------------------------------------------ 내부

    def _read_token_record(self) -> dict:
        try:
            proc = subprocess.run(
                ["node", self.decrypt_script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
        except Exception as e:
            raise GoogleAuthError(f"토큰 복호화 스크립트 실행 실패: {e}") from e

        if proc.returncode != 0:
            raise GoogleAuthError(
                f"토큰 복호화 실패 (exit {proc.returncode}): {(proc.stderr or '')[:200]}"
            )
        try:
            data = json.loads((proc.stdout or "").strip())
        except json.JSONDecodeError as e:
            raise GoogleAuthError(f"토큰 저장소 JSON 파싱 실패: {e}") from e

        record = (data or {}).get(self.account) or {}
        token = record.get("token") or {}
        if not token.get("accessToken"):
            raise GoogleAuthError(
                f"계정 {self.account!r} 의 accessToken 을 찾지 못했습니다."
            )
        return token

    def _is_expiring(self, token: dict) -> bool:
        expires_at = token.get("expiresAt")
        if not expires_at:
            # 만료 정보가 없으면 만료로 간주하고 갱신을 시도한다 (조용한 401 방지)
            return True
        return (expires_at / 1000) - time.time() <= self.skew_seconds

    # ------------------------------------------------------------------ 공개 API

    def get_token(self) -> str:
        token = self._read_token_record()
        if not self._is_expiring(token):
            return token["accessToken"]

        if not self.refresh_callback:
            raise GoogleAuthError(
                "액세스 토큰이 만료되었고 갱신 수단이 없습니다. "
                "MCP auth.refreshToken 을 호출할 수 있는 refresh_callback 을 주입하십시오."
            )

        print(
            "[GoogleTokenProvider] 액세스 토큰 만료 임박 — MCP auth.refreshToken 으로 갱신합니다.",
            file=sys.stderr,
        )
        try:
            self.refresh_callback()
        except Exception as e:
            raise GoogleAuthError(f"토큰 갱신 호출 실패: {e}") from e

        token = self._read_token_record()
        if self._is_expiring(token):
            raise GoogleAuthError(
                "갱신 후에도 액세스 토큰이 만료 상태입니다. 재인증이 필요할 수 있습니다."
            )
        return token["accessToken"]
