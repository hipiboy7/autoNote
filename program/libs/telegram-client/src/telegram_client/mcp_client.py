import os
import json
import sys
import subprocess
from datetime import datetime
from typing import List, Union
from .interface import TelegramClient, TelegramMessage

class MCPTelegramClient(TelegramClient):
    def __init__(self, mcp_dir: str = "D:\\autoNote\\telegram-mcp"):
        self.mcp_dir = mcp_dir
        # 가상환경 파이썬 또는 기본 파이썬 설정
        self.venv_python = os.path.join(mcp_dir, "venv", "Scripts", "python.exe")
        if not os.path.exists(self.venv_python):
            # 윈도우/리눅스 호환을 위한 폴백
            self.venv_python = os.path.join(mcp_dir, ".venv", "Scripts", "python.exe")
        if not os.path.exists(self.venv_python):
            # 최종 폴백: 시스템 기본 파이썬
            self.venv_python = sys.executable

        self.main_script = os.path.join(mcp_dir, "main.py")

    def list_messages(self, chat_id: Union[int, str], limit: int = 100) -> List[TelegramMessage]:
        # Stdio로 MCP 서버 구동
        env = os.environ.copy()
        env["PYTHONPATH"] = self.mcp_dir
        
        cmd = [self.venv_python, self.main_script]
        
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1, # Line buffered
            env=env
        )

        try:
            # 1. MCP initialize handshake
            init_req = {
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "capabilities": {},
                    "clientInfo": {"name": "autoNote-collector", "version": "1.0"},
                    "protocolVersion": "2024-11-05"
                },
                "id": 1
            }
            proc.stdin.write(json.dumps(init_req) + "\n")
            proc.stdin.flush()
            
            # 응답 대기
            init_res_line = proc.stdout.readline()
            if not init_res_line:
                raise RuntimeError("Failed to receive initialize response from Telegram MCP server.")
            
            # 2. initialized notification 전송
            initialized_notif = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized"
            }
            proc.stdin.write(json.dumps(initialized_notif) + "\n")
            proc.stdin.flush()

            # 3. tools/call - list_messages 호출
            # chat_id 타입 변환: 문자열 숫자인 경우 int로 파싱
            if isinstance(chat_id, str):
                try:
                    if chat_id.startswith("-") or chat_id.isdigit():
                        chat_id = int(chat_id)
                except ValueError:
                    pass

            call_req = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "list_messages",
                    "arguments": {
                        "chat_id": chat_id,
                        "limit": limit
                    }
                },
                "id": 2
            }
            proc.stdin.write(json.dumps(call_req) + "\n")
            proc.stdin.flush()

            # 결과 대기
            call_res_line = proc.stdout.readline()
            if not call_res_line:
                raise RuntimeError("Failed to receive tool execution response from Telegram MCP server.")

            call_res = json.loads(call_res_line)
            
            # 에러 검사
            if "error" in call_res:
                raise RuntimeError(f"MCP Tool execution error: {call_res['error']}")

            # MCP Result content 파싱
            result = call_res.get("result", {})
            content = result.get("content", [])
            if not content:
                # 결과물이 비어 있을 때
                return []

            text_payload = content[0].get("text", "")
            if not text_payload:
                return []

            # 텔레그램 MCP의 format_tool_result 포맷은 JSON 스트링임
            # {"results": [{"id": 123, "sender": "...", "date": "...", "text": "..."}]}
            payload_data = json.loads(text_payload)
            records = payload_data.get("results", [])
            
            # 4. JSON records -> TelegramMessage 객체 변환
            messages = []
            for rec in records:
                # date 파싱 ("2026-07-15T12:00:00Z" 등)
                date_str = rec.get("date", "")
                if date_str:
                    # ISO 포맷 파싱
                    try:
                        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    except ValueError:
                        dt = datetime.now(timezone.utc)
                else:
                    dt = datetime.now(timezone.utc)

                msg = TelegramMessage(
                    id=rec.get("id"),
                    channel_id=str(chat_id),
                    text=rec.get("text", ""),
                    date=dt,
                    sender_id=str(rec.get("sender", ""))
                )
                messages.append(msg)

            return messages

        finally:
            # 5. 프로세스 종료 처리
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
