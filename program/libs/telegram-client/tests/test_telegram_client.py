import json
from unittest.mock import MagicMock, patch

import pytest
from telegram_client.mcp_client import MCPTelegramClient


def rpc(id_, result):
    return json.dumps({"jsonrpc": "2.0", "id": id_, "result": result}) + "\n"


def messages_payload(records):
    return {"content": [{"type": "text", "text": json.dumps({"results": records},
                                                            ensure_ascii=False)}]}


def make_proc(lines):
    proc = MagicMock()
    proc.stdout.readline.side_effect = list(lines) + [""]
    return proc


def written(proc):
    return "".join(call.args[0] for call in proc.stdin.write.call_args_list)


def sent_arguments(proc):
    """클라이언트가 실제로 보낸 tools/call 의 arguments 를 파싱해 돌려준다.

    JSON 직렬화 시 한글이 \\uXXXX 로 이스케이프되므로 문자열 포함 검사에 의존하지 않는다.
    """
    for line in written(proc).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("method") == "tools/call":
            return payload["params"]["arguments"]
    raise AssertionError("tools/call 요청이 전송되지 않았습니다.")


# --------------------------------------------------------------- list_messages

@patch("telegram_client.mcp_client.subprocess.Popen")
def test_list_messages_parses_records(mock_popen):
    proc = make_proc([
        rpc(1, {}),
        rpc(2, messages_payload([
            {"id": 765, "sender": "peter", "date": "2026-08-04T12:07:53Z", "text": "코멘트"},
            {"id": 766, "sender": "peter", "date": "2026-08-04T12:08:02Z", "text": "기존 의견"},
        ])),
    ])
    mock_popen.return_value = proc

    client = MCPTelegramClient(mcp_dir="D:\\autoNote\\telegram-mcp")
    msgs = client.list_messages("-1004421517798", limit=200)

    assert [m.id for m in msgs] == [765, 766]
    assert msgs[0].text == "코멘트"
    assert msgs[0].date.year == 2026
    assert "list_messages" in written(proc)
    # 숫자 문자열 채널 ID 는 int 로 변환되어야 한다
    assert sent_arguments(proc)["chat_id"] == -1004421517798


@patch("telegram_client.mcp_client.subprocess.Popen")
def test_non_json_stdout_lines_are_skipped(mock_popen):
    """MCP 서버가 안내 문구를 stdout 에 섞어 내보내도 파싱이 깨지지 않아야 한다."""
    proc = make_proc([
        "Starting Telegram MCP server...\n",
        rpc(1, {}),
        "session loaded\n",
        rpc(2, messages_payload([{"id": 1, "date": "", "text": "x"}])),
    ])
    mock_popen.return_value = proc

    msgs = MCPTelegramClient(mcp_dir="x").list_messages("-100", limit=1)
    assert [m.id for m in msgs] == [1]


@patch("telegram_client.mcp_client.subprocess.Popen")
def test_empty_content_returns_empty_list(mock_popen):
    mock_popen.return_value = make_proc([rpc(1, {}), rpc(2, {"content": []})])
    assert MCPTelegramClient(mcp_dir="x").list_messages("-100") == []


@patch("telegram_client.mcp_client.subprocess.Popen")
def test_tool_error_raises(mock_popen):
    proc = make_proc([
        rpc(1, {}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "error": {"message": "chat not found"}}) + "\n",
    ])
    mock_popen.return_value = proc

    with pytest.raises(RuntimeError) as exc:
        MCPTelegramClient(mcp_dir="x").list_messages("-100")
    assert "chat not found" in str(exc.value)


# --------------------------------------------------------------- send_message

@patch("telegram_client.mcp_client.subprocess.Popen")
def test_send_message_to_self(mock_popen):
    """'me' 는 본인 저장 메시지로, 배치 실패 알림을 폰으로 받는 경로다."""
    proc = make_proc([rpc(1, {}), rpc(2, {"content": [{"text": "Message sent"}]})])
    mock_popen.return_value = proc

    ok = MCPTelegramClient(mcp_dir="x").send_message("me", "배치 실패 알림")

    assert ok is True
    assert "send_message" in written(proc)
    args = sent_arguments(proc)
    assert args["chat_id"] == "me"          # 별칭은 int 변환하지 않는다
    assert args["message"] == "배치 실패 알림"


@patch("telegram_client.mcp_client.subprocess.Popen")
def test_send_message_returns_false_on_exception(mock_popen, capsys):
    """알림 전송 실패가 배치를 중단시켜서는 안 된다."""
    mock_popen.side_effect = OSError("telegram mcp 실행 불가")

    ok = MCPTelegramClient(mcp_dir="x").send_message("me", "본문")

    assert ok is False
    assert "send_message 실패" in capsys.readouterr().err


@patch("telegram_client.mcp_client.subprocess.Popen")
def test_send_message_detects_failure_in_response(mock_popen):
    proc = make_proc([rpc(1, {}), rpc(2, {"content": [{"text": "Error: peer not found"}]})])
    mock_popen.return_value = proc

    assert MCPTelegramClient(mcp_dir="x").send_message("me", "본문") is False


def test_send_message_rejects_blank_text():
    assert MCPTelegramClient(mcp_dir="x").send_message("me", "   ") is False


@patch("telegram_client.mcp_client.subprocess.Popen")
def test_parse_mode_is_forwarded_when_given(mock_popen):
    proc = make_proc([rpc(1, {}), rpc(2, {"content": [{"text": "ok"}]})])
    mock_popen.return_value = proc

    MCPTelegramClient(mcp_dir="x").send_message("me", "<b>굵게</b>", parse_mode="html")

    assert sent_arguments(proc)["parse_mode"] == "html"
