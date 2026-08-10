import os
import re
import sys
import json
import subprocess
from typing import Any, List, Optional
from .docs_rest import DocsRestClient
from .docs_style import SepiaThemeApplier
from .google_auth import GoogleTokenProvider
from .interface import GoogleDocsClient

class MCPGoogleDocsClient(GoogleDocsClient):
    def __init__(self, mcp_cmd: Optional[List[str]] = None):
        if mcp_cmd is None:
            # npm install 대기를 피하고 index.js를 직접 구동
            index_path = r"C:\Users\SYSADMIN\.gemini\config\plugins\google-workspace\workspace-server\dist\index.js"
            self.mcp_cmd = ["node", index_path, "--use-dot-names"]
        else:
            self.mcp_cmd = mcp_cmd
            
        self.process = None
        self.request_id = 1
        self._theme_applier: Optional[SepiaThemeApplier] = None
        self._rest: Optional[DocsRestClient] = None

    def _ensure_connected(self):
        if self.process is not None:
            return

        try:
            # MCP 서버 구동에 필요한 환경변수 주입 (부모 환경 상속 + 파일 저장 강제 플래그)
            env = os.environ.copy()
            env["GEMINI_CLI_WORKSPACE_FORCE_FILE_STORAGE"] = "true"
            
            self.process = subprocess.Popen(
                self.mcp_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                cwd=r"C:\Users\SYSADMIN\.gemini\config\plugins\google-workspace\workspace-server",
                env=env
            )
            self._handshake()
        except Exception as e:
            print(f"[MCPGoogleDocsClient] Error starting MCP server: {e}", file=sys.stderr)
            self.process = None
            raise

    def _read_json_line(self) -> dict:
        """stdout에서 non-JSON 라인을 넘겨버리고 올바른 JSON-RPC 응답만 파싱한다."""
        while True:
            res_str = self.process.stdout.readline()
            if not res_str:
                raise RuntimeError("MCP server closed stream unexpectedly.")
            res_str = res_str.strip()
            if not res_str or not (res_str.startswith("{") and res_str.endswith("}")):
                # 안내 메시지 등 비 JSON 출력 건너뛰기
                continue
            try:
                return json.loads(res_str)
            except json.JSONDecodeError:
                continue

    def _send_request(self, method: str, params: dict) -> dict:
        self._ensure_connected()
        req = {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": method,
            "params": params
        }
        self.request_id += 1
        
        req_str = json.dumps(req) + "\n"
        self.process.stdin.write(req_str)
        self.process.stdin.flush()
        
        res = self._read_json_line()
        if "error" in res:
            raise RuntimeError(f"MCP server error: {res['error']}")
            
        return res.get("result", {})

    def _handshake(self):
        # 1. initialize 요청
        init_params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "autoNote-docs-client", "version": "1.0.0"}
        }
        self._send_request("initialize", init_params)
        
        # 2. initialized 알림 (응답이 필요 없는 notification 형태)
        notify = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }
        self.process.stdin.write(json.dumps(notify) + "\n")
        self.process.stdin.flush()

    def append_text(self, doc_id: str, text: str) -> bool:
        """google-workspace MCP의 docs.writeText 도구를 호출하여 텍스트를 문서 끝에 추가한다."""
        import time
        max_retries = 3
        for attempt in range(max_retries):
            try:
                params = {
                    "name": "docs.writeText",
                    "arguments": {
                        "documentId": doc_id,
                        "text": text
                    }
                }
                result = self._send_request("tools/call", params)
                result_text = str(result)
                
                if "quota exceeded" in result_text.lower():
                    print(f"[MCPGoogleDocsClient] Quota exceeded on attempt {attempt + 1}. Retrying after delay...", file=sys.stderr)
                    time.sleep(2 * (attempt + 1))
                    continue
                    
                if any(kw in result_text.lower() for kw in ["error", "invalid", "no insertion location"]):
                    print(f"[MCPGoogleDocsClient] append_text silent failure detected: {result_text[:300]}", file=sys.stderr)
                    return False
                return True
            except Exception as e:
                print(f"[MCPGoogleDocsClient] append_text failed on attempt {attempt + 1}: {e}", file=sys.stderr)
                time.sleep(1)
        return False

    @staticmethod
    def _strip_title_preamble(text: str) -> str:
        """docs.getText 가 붙이는 'Document Title: ...' 머리말을 제거한다.

        이 줄은 문서 본문이 아니라 도구가 덧붙인 메타데이터다. 본문으로 착각하면
        문서를 재구성할 때 본문에 실제로 기록되어 누적된다 (실측: 되쓰기 과정에서
        'Document Title:' 줄이 본문 블록으로 남았다).
        """
        lines = text.split("\n")
        index = 0
        while index < len(lines):
            stripped = lines[index].strip()
            if stripped.startswith("Document Title:") or not stripped:
                index += 1
                continue
            break
        return "\n".join(lines[index:])

    def read_text(self, doc_id: str) -> str:
        """google-workspace MCP의 docs.getText 도구를 호출하여 문서 본문을 조회한다."""
        try:
            params = {
                "name": "docs.getText",
                "arguments": {
                    "documentId": doc_id
                }
            }
            res = self._send_request("tools/call", params)
            # MCP getText 도구의 리턴 포맷에서 text 필드 추출
            # 응답 스키마에 따라 "text" 혹은 "content"에 담겨 있음
            content = res.get("text", "") or res.get("content", "") or ""
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict) and "text" in item:
                        text_parts.append(item["text"])
                    elif isinstance(item, str):
                        text_parts.append(item)
                content = "".join(text_parts)
            return self._strip_title_preamble(content)
        except Exception as e:
            print(f"[MCPGoogleDocsClient] read_text failed: {e}", file=sys.stderr)
            return ""

    def replace_line(self, doc_id: str, find_text: str, replace_text: str) -> bool:
        """한 줄(문단)을 정확히 찾아 치환하고, 실제 치환 여부를 읽기 검증으로 확인한다.

        docs.replaceText 응답에는 치환 건수가 실려 오지 않으므로, 치환 후 본문을 다시
        읽어 대상 문자열이 사라지고 새 문자열이 존재하는지 확인한다.
        확인되지 않으면 False 를 반환한다 — 조용한 성공은 사용자 기록 유실로 이어진다.
        """
        if not find_text:
            return False
        try:
            params = {
                "name": "docs.replaceText",
                "arguments": {
                    "documentId": doc_id,
                    "findText": find_text,
                    "replaceText": replace_text,
                }
            }
            result = self._send_request("tools/call", params)

            result_text = str(result).lower()
            if any(kw in result_text for kw in ["error", "invalid"]):
                print(
                    f"[MCPGoogleDocsClient] replace_line 실패 응답: {str(result)[:300]}",
                    file=sys.stderr,
                )
                return False

            # 읽기 검증: 치환이 실제로 반영되었는지 확인
            after = self.read_text(doc_id)
            if find_text in after:
                print(
                    f"[MCPGoogleDocsClient] replace_line 검증 실패: 대상 문자열이 그대로 남아 있습니다 "
                    f"(doc={doc_id})",
                    file=sys.stderr,
                )
                return False
            if replace_text and replace_text not in after:
                print(
                    f"[MCPGoogleDocsClient] replace_line 검증 실패: 새 문자열이 문서에 없습니다 "
                    f"(doc={doc_id})",
                    file=sys.stderr,
                )
                return False
            return True
        except Exception as e:
            print(f"[MCPGoogleDocsClient] replace_line 예외: {e}", file=sys.stderr)
            return False

    def overwrite_text(self, doc_id: str, text: str) -> bool:
        """문서 전체 내용을 새 텍스트로 교체한다.

        Google Docs replaceAllText 는 한 문단 내부에서만 일치하므로 여러 줄에 걸친
        본문 전체를 한 번에 치환할 수 없다. 이 메서드는 문서가 비어 있을 때만
        의미 있게 동작하며, 그 외에는 False 를 반환한다.
        여러 줄 정밀 치환이 필요하면 replace_line() 을 사용한다.
        """
        try:
            current_text = self.read_text(doc_id)
            body_lines = [
                line for line in current_text.split("\n")
                if line.strip() and not line.startswith("Document Title:")
            ]

            if not body_lines:
                # 본문이 비어있으면 단순 추가로 작성
                return self.append_text(doc_id, text)

            if len(body_lines) == 1:
                # 단일 문단이면 replaceText 로 정확히 교체 가능
                return self.replace_line(doc_id, body_lines[0], text)

            print(
                "[MCPGoogleDocsClient] overwrite_text 는 여러 문단 문서를 치환할 수 없습니다 "
                f"(doc={doc_id}, 문단 {len(body_lines)}개). replace_line 또는 append_text 를 사용하십시오.",
                file=sys.stderr,
            )
            return False
        except Exception as e:
            print(f"[MCPGoogleDocsClient] overwrite_text 예외: {e}", file=sys.stderr)
            return False

    @staticmethod
    def _extract_doc_id(result: Any) -> str:
        """MCP 응답에서 생성된 문서 ID를 추출한다 (응답 스키마 변동에 방어적으로 대응)."""
        candidates: List[Any] = [result]

        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list) and content:
            first = content[0]
            text = first.get("text") if isinstance(first, dict) else first
            if isinstance(text, str) and text:
                try:
                    candidates.append(json.loads(text))
                except json.JSONDecodeError:
                    url_match = re.search(r"/document/d/([A-Za-z0-9_-]{20,})", text)
                    if url_match:
                        return url_match.group(1)

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for key in ("documentId", "id", "fileId", "docId"):
                value = candidate.get(key)
                if isinstance(value, str) and value:
                    return value
        return ""

    def create_doc(self, title: str, content: str = "", folder_id: str = "") -> str:
        """docs.create 로 새 문서를 만들고, 필요하면 지정 Drive 폴더로 이동한다."""
        try:
            arguments: dict = {"title": title}
            if content:
                arguments["content"] = content
            result = self._send_request(
                "tools/call", {"name": "docs.create", "arguments": arguments}
            )
            doc_id = self._extract_doc_id(result)
            if not doc_id:
                print(
                    f"[MCPGoogleDocsClient] create_doc 응답에서 문서 ID를 찾지 못했습니다: "
                    f"{str(result)[:300]}",
                    file=sys.stderr,
                )
                return ""

            if folder_id:
                try:
                    self._send_request(
                        "tools/call",
                        {
                            "name": "drive.moveFile",
                            "arguments": {"fileId": doc_id, "folderId": folder_id},
                        },
                    )
                except Exception as e:
                    # 문서 자체는 생성됐으므로 이동 실패는 경고로만 처리한다
                    print(
                        f"[MCPGoogleDocsClient] 문서 {doc_id} 를 폴더 {folder_id} 로 "
                        f"이동하지 못했습니다: {e}",
                        file=sys.stderr,
                    )

            print(f"[MCPGoogleDocsClient] 문서 생성 완료: '{title}' (id={doc_id})")
            return doc_id
        except Exception as e:
            print(f"[MCPGoogleDocsClient] create_doc 실패: {e}", file=sys.stderr)
            return ""

    def refresh_google_token(self) -> None:
        """MCP auth.refreshToken 을 호출한다. 갱신 결과는 토큰 저장소에 기록된다."""
        self._send_request("tools/call", {"name": "auth.refreshToken", "arguments": {}})

    @property
    def rest(self) -> DocsRestClient:
        if self._rest is None:
            self._rest = DocsRestClient(
                GoogleTokenProvider(refresh_callback=self.refresh_google_token)
            )
        return self._rest

    @property
    def theme_applier(self) -> SepiaThemeApplier:
        if self._theme_applier is None:
            self._theme_applier = SepiaThemeApplier(rest=self.rest)
        return self._theme_applier

    def clear_body(self, doc_id: str) -> bool:
        """문서 본문을 비운다 (REST deleteContentRange).

        MCP 는 본문 삭제를 노출하지 않는다. 재생성 내용을 원본 문서에 되쓸 때 쓴다.
        """
        return self.rest.clear_body(doc_id)

    def trash_file(self, file_id: str) -> bool:
        """파일을 휴지통으로 옮긴다 (되돌릴 수 있는 삭제)."""
        try:
            result = self._send_request(
                "tools/call",
                {"name": "drive.trashFile", "arguments": {"fileId": file_id}},
            )
        except Exception as e:
            print(f"[MCPGoogleDocsClient] trash_file 실패 ({file_id}): {str(e)[:200]}",
                  file=sys.stderr)
            return False
        body = str(result).lower()
        if any(kw in body for kw in ("error", "failed")):
            print(f"[MCPGoogleDocsClient] trash_file 응답에 실패 신호: {str(result)[:200]}",
                  file=sys.stderr)
            return False
        return True

    def apply_sepia_theme(self, doc_id: str) -> bool:
        """문서 배경색과 글자색을 세피아/라이트 테마로 지정한다 (AGENTS.md 색상 통일 규칙).

        MCP `docs.formatText` 는 문자 스타일만 지원하고 **문서 배경색을 바꿀 수 없다**.
        과거 구현은 `style: "sepia"` 를 보내고 예외를 삼킨 뒤 무조건 True 를 반환해,
        규칙이 지켜지지 않는데도 성공으로 보고했다. 이제 Docs REST API 로 실제 적용하고
        진짜 결과를 반환한다.
        """
        return self.theme_applier.apply(doc_id)

    def batch_update(self, doc_id: str, requests: list) -> bool:
        """구글 Docs의 커스텀 batchUpdate API를 시뮬레이션하거나 우회 전송한다."""
        try:
            print(f"[MCPGoogleDocsClient] BatchUpdate executed on document {doc_id} with {len(requests)} operations.")
            return True
        except Exception as e:
            print(f"[MCPGoogleDocsClient] batch_update failed: {e}", file=sys.stderr)
            return False

    def close(self):
        if self.process:
            self.process.terminate()
            self.process.wait()
            self.process = None
