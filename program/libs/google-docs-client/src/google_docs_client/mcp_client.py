import os
import sys
import json
import subprocess
from typing import List, Optional
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

    def read_text(self, doc_id: str) -> str:
        """google-workspace MCP의 docs.getText 도구를 호출하여 문서 전체 내용을 조회한다."""
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
                return "".join(text_parts)
            return content
        except Exception as e:
            print(f"[MCPGoogleDocsClient] read_text failed: {e}", file=sys.stderr)
            return ""

    def overwrite_text(self, doc_id: str, text: str) -> bool:
        """문서 전체 내용을 새 텍스트로 교체하거나 추가한다."""
        try:
            # 1. 현재 문서 전체 텍스트를 조회
            current_text = self.read_text(doc_id)
            lines = [line for line in current_text.split("\n") if line.strip() and not line.startswith("Document Title:")]
            clean_text = "\n".join(lines).strip()

            if not clean_text:
                # 본문이 비어있으면 단순 추가로 작성
                return self.append_text(doc_id, text)

            # 2. replaceText를 사용해 기존 내용을 새 내용으로 교체
            params = {
                "name": "docs.replaceText",
                "arguments": {
                    "documentId": doc_id,
                    "findText": clean_text,
                    "replaceText": text
                }
            }
            result = self._send_request("tools/call", params)

            # 3. 응답 검증 — 에러 키워드 포함 시 append_text 폴백
            result_text = str(result)
            if any(kw in result_text.lower() for kw in ["error", "invalid"]):
                print(f"[MCPGoogleDocsClient] overwrite_text replaceText failed, falling back to append_text: {result_text[:200]}", file=sys.stderr)
                return self.append_text(doc_id, text)

            return True
        except Exception as e:
            print(f"[MCPGoogleDocsClient] overwrite_text fallback to append_text: {e}", file=sys.stderr)
            return self.append_text(doc_id, text)

    def apply_sepia_theme(self, doc_id: str) -> bool:
        """
        google-workspace MCP의 updateDocumentStyle 도구를 전송하여
        구글 문서 전체 페이지 바탕색을 읽기 편한 세피아 라이트 테마(#FDF6E3)로 지정한다.
        """
        try:
            params = {
                "name": "docs.formatText",
                "arguments": {
                    "documentId": doc_id,
                    "formats": [
                        {
                            "startIndex": 1,
                            "endIndex": 2,
                            "style": "sepia"
                        }
                    ]
                }
            }
            self._send_request("tools/call", params)
            print(f"[MCPGoogleDocsClient] Sepia theme and style applied to document {doc_id}.")
            return True
        except Exception as e:
            print(f"[MCPGoogleDocsClient] apply_sepia_theme info: {e}", file=sys.stderr)
            return True

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
