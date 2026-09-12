import json
import os
import subprocess
import sys
import threading
import queue
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .interface import TelegramClient, TelegramMessage

# 저장소 루트를 이 파일 위치에서 역산한다.
#   .../packages/libs/telegram-client/src/telegram_client/mcp_client.py
#    parents[5] ------------------------------------------------ 저장소 루트
# 절대 경로를 박아 두면 디렉토리를 옮길 때 조용히 깨진다 (실제로 2026-08-08~10 배치가
# 이 이유로 3일간 실패했다). TELEGRAM_MCP_DIR 로 언제든 덮어쓸 수 있다.
_REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_MCP_DIR = str(_REPO_ROOT / "vendor" / "telegram-mcp")


# ---------------------------------------------------------------- 응답 대기 상한
# 2026-09-12 배치가 Google Docs MCP 응답을 기다리며 6시간 넘게 멈췄다. readline() 에
# 타임아웃이 없어 서버가 침묵하면 배치가 영원히 잠금을 쥔 채 서 있고, 다음 날 예약
# 실행까지 IgnoreNew 로 건너뛰어진다. 응답 대기에는 반드시 상한을 둔다.
# (google-docs-client 에도 같은 헬퍼가 있다. 라이브러리 간 의존을 만들지 않기 위해 둘로 둔다.)
DEFAULT_MCP_READ_TIMEOUT_SECONDS = 120.0


class MCPReadTimeout(RuntimeError):
    """MCP 서버가 제한 시간 안에 한 줄도 응답하지 않았다."""


def read_timeout_seconds() -> float:
    raw = os.getenv("MCP_READ_TIMEOUT_SECONDS")
    if not raw:
        return DEFAULT_MCP_READ_TIMEOUT_SECONDS
    try:
        return max(0.1, float(raw))
    except ValueError:
        return DEFAULT_MCP_READ_TIMEOUT_SECONDS


def readline_with_timeout(stream, timeout: float) -> str:
    """stream.readline() 을 별도 스레드에서 수행하고 timeout 안에 못 받으면 예외를 낸다.

    Windows 파이프는 select 가 안 되므로 스레드+큐로 상한을 건다. 타임아웃이 나면
    읽기 스레드는 파이프에 매달린 채 남는데, 호출자가 서버 프로세스를 죽이면 EOF 를
    받고 스스로 끝난다. 스트림이 닫혔거나(mock 소진 포함) 읽기 자체가 실패하면 EOF("") 로
    본다 — 기존 '스트림 닫힘' 처리 경로를 그대로 타게 하기 위해서다.
    """
    box: "queue.Queue[str]" = queue.Queue(maxsize=1)

    def _reader() -> None:
        try:
            box.put(stream.readline())
        except Exception:  # noqa: BLE001 - StopIteration/ValueError 등은 곧 EOF 다
            box.put("")

    threading.Thread(target=_reader, daemon=True).start()
    try:
        return box.get(timeout=timeout)
    except queue.Empty:
        raise MCPReadTimeout(
            f"MCP 서버가 {timeout:.0f}초 안에 응답하지 않았습니다. "
            "서버 프로세스를 종료하고 이 호출을 실패로 처리합니다."
        ) from None


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
            # 타임아웃이면 MCPReadTimeout 이 올라가고, _call_tool 의 finally 가 서버를 종료한다.
            line = readline_with_timeout(proc.stdout, read_timeout_seconds())
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

        # 경로가 틀리면 Popen 은 뜨자마자 죽고, 아래 핸드셰이크가
        # "응답 없이 스트림을 닫았습니다" 로만 보고돼 진짜 원인이 가려진다.
        if not os.path.exists(self.main_script):
            raise RuntimeError(
                f"Telegram MCP 서버 스크립트를 찾을 수 없습니다: {self.main_script} "
                f"(mcp_dir={self.mcp_dir}). TELEGRAM_MCP_DIR 을 확인하십시오."
            )

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
