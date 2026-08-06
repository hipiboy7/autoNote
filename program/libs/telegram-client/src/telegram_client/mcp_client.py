import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from .interface import TelegramClient, TelegramMessage

DEFAULT_MCP_DIR = r"D:\autoNote\telegram-mcp"


class MCPTelegramClient(TelegramClient):
    def __init__(self, mcp_dir: Optional[str] = None):
        self.mcp_dir = mcp_dir or os.getenv("TELEGRAM_MCP_DIR", DEFAULT_MCP_DIR)
        # 가상환경 파이썬 또는 기본 파이썬 설정
        self.venv_python = os.path.join(self.mcp_dir, "venv", "Scripts", "python.exe")
        if not os.path.exists(self.venv_python):
            # 윈도우/리눅스 호환을 위한 폴백
            self.venv_python = os.path.join(self.mcp_dir, ".venv", "Scripts", "python.exe")
        if not os.path.exists(self.venv_python):
            # 최종 폴백: 시스템 기본 파이썬
            self.venv_python = sys.executable

        self.main_script = os.path.join(self.mcp_dir, "main.py")

    # ------------------------------------------------------------------ 내부

    @staticmethod
    def _normalize_chat_id(chat_id: Union[int, str]) -> Union[int, str]:
        """숫자 문자열 채널 ID 는 int 로 바꾼다. 'me' 같은 별칭은 그대로 둔다."""
        if isinstance(chat_id, str):
            candidate = chat_id.strip()
            if candidate.lstrip("-").isdigit():
                try:
                    return int(candidate)
                except ValueError:
                    return candidate
            return candidate
        return chat_id

    def _read_json_line(self, proc: subprocess.Popen, want_id: int) -> Dict[str, Any]:
        """원하는 id 의 JSON-RPC 응답만 골라 읽는다.

        MCP 서버가 stdout 에 안내 문구나 알림(notification)을 섞어 내보내는 경우가 있어,
        한 줄만 읽고 파싱하면 깨진다.
        """
        while True:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError(
                    "Telegram MCP 서버가 응답 없이 스트림을 닫았습니다."
                )
            line = line.strip()
            if not (line.startswith("{") and line.endswith("}")):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("id") == want_id:
                return payload

    def _call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Telegram MCP 서버를 stdio 로 띄워 도구 하나를 호출하고 result 를 반환한다."""
        env = os.environ.copy()
        env["PYTHONPATH"] = self.mcp_dir

        proc = subprocess.Popen(
            [self.venv_python, self.main_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,  # Line buffered
            env=env,
        )

        try:
            # 1. initialize 핸드셰이크
            proc.stdin.write(json.dumps({
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "capabilities": {},
                    "clientInfo": {"name": "autoNote-collector", "version": "1.0"},
                    "protocolVersion": "2024-11-05",
                },
                "id": 1,
            }) + "\n")
            proc.stdin.flush()
            self._read_json_line(proc, 1)

            # 2. initialized 알림
            proc.stdin.write(json.dumps({
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }) + "\n")
            proc.stdin.flush()

            # 3. tools/call
            proc.stdin.write(json.dumps({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
                "id": 2,
            }) + "\n")
            proc.stdin.flush()

            response = self._read_json_line(proc, 2)
            if "error" in response:
                raise RuntimeError(f"MCP Tool execution error: {response['error']}")
            return response.get("result", {})

        finally:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                proc.kill()

    @staticmethod
    def _result_text(result: Dict[str, Any]) -> str:
        content = result.get("content", [])
        if not content:
            return ""
        first = content[0]
        if isinstance(first, dict):
            return first.get("text", "") or ""
        return str(first)

    # ------------------------------------------------------------------ 공개 API

    def list_messages(
        self, chat_id: Union[int, str], limit: int = 100
    ) -> List[TelegramMessage]:
        normalized = self._normalize_chat_id(chat_id)
        result = self._call_tool(
            "list_messages", {"chat_id": normalized, "limit": limit}
        )

        text_payload = self._result_text(result)
        if not text_payload:
            return []

        # 텔레그램 MCP 의 format_tool_result 포맷은 JSON 스트링이다
        # {"results": [{"id": 123, "sender": "...", "date": "...", "text": "..."}]}
        payload_data = json.loads(text_payload)
        records = payload_data.get("results", [])

        messages: List[TelegramMessage] = []
        for rec in records:
            date_str = rec.get("date", "")
            dt = datetime.now(timezone.utc)
            if date_str:
                try:
                    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                except ValueError:
                    pass

            messages.append(TelegramMessage(
                id=rec.get("id"),
                channel_id=str(normalized),
                text=rec.get("text", ""),
                date=dt,
                sender_id=str(rec.get("sender", "")),
            ))
        return messages

    def send_message(
        self,
        chat_id: Union[int, str],
        text: str,
        parse_mode: Optional[str] = None,
    ) -> bool:
        """지정 대화에 메시지를 보낸다. 실패는 예외 대신 False 로 알린다.

        `chat_id="me"` 는 본인 저장 메시지(Saved Messages)로, 배치 실패 알림을
        휴대폰으로 즉시 받는 경로다. 알림 전송 실패가 배치를 중단시켜서는 안 되므로
        여기서 예외를 흘리지 않는다.
        """
        if not text.strip():
            return False
        arguments: Dict[str, Any] = {
            "chat_id": self._normalize_chat_id(chat_id),
            "message": text,
        }
        if parse_mode:
            arguments["parse_mode"] = parse_mode

        try:
            result = self._call_tool("send_message", arguments)
        except Exception as e:
            print(f"[MCPTelegramClient] send_message 실패: {str(e)[:300]}", file=sys.stderr)
            return False

        body = self._result_text(result).lower()
        if any(keyword in body for keyword in ("error", "failed", "실패")):
            print(
                f"[MCPTelegramClient] send_message 응답에 실패 신호: "
                f"{self._result_text(result)[:200]}",
                file=sys.stderr,
            )
            return False
        return True
