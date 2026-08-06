import json
import pytest
from unittest.mock import patch, MagicMock
from google_docs_client.mcp_client import MCPGoogleDocsClient

@pytest.fixture
def mock_subprocess():
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc
        
        # 1. initialize 및 tools/call 응답 mock 설정
        # readline 호출 시 차례대로 응답하도록 side_effect 지정
        mock_proc.stdout.readline.side_effect = [
            # initialize 응답
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}),
            # append_text (tools/call) 응답
            json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "Success"}]}})
        ]
        yield mock_popen, mock_proc

def test_mcp_client_handshake_and_append_text(mock_subprocess):
    mock_popen, mock_proc = mock_subprocess
    
    # 1. MCPGoogleDocsClient 기동
    client = MCPGoogleDocsClient(mcp_cmd=["mock-node", "mock-script.js"])
    
    # 2. append_text 실행 (이때 Popen 기동 및 handshake 자동 진행)
    res = client.append_text("doc-123", "Hello Google Docs")
    
    # 3. 검증
    assert res is True
    
    # Popen이 지정된 모킹 인자로 기동되었는지 검증
    mock_popen.assert_called_once()
    assert mock_popen.call_args[0][0] == ["mock-node", "mock-script.js"]
    
    # Stdin을 통해 전송된 JSON-RPC 요청 문자열 검증
    written_data = "".join([call.args[0] for call in mock_proc.stdin.write.call_args_list])
    
    # initialize 요청, initialized 알림, tools/call 요청이 쏘아졌는지 확인
    assert "initialize" in written_data
    assert "notifications/initialized" in written_data
    assert "tools/call" in written_data
    assert "docs.writeText" in written_data
    assert "Hello Google Docs" in written_data

    # 정리
    client.close()
    mock_proc.terminate.assert_called_once()

@patch("subprocess.Popen")
def test_read_text_strips_document_title_preamble(mock_popen):
    """docs.getText 가 붙이는 'Document Title:' 은 본문이 아니다.

    본문으로 착각하면 문서를 재구성할 때 실제 본문에 기록되어 누적된다
    (실측: 되쓰기 후 본문에 'Document Title:' 줄 2개가 남았다).
    """
    mock_proc = MagicMock()
    mock_popen.return_value = mock_proc
    body = "# 10_테마 금리\n위에서 아래로 시간순 누적.\n━━━\n■ [r518] 2026-07-26 | 제목\n"
    mock_proc.stdout.readline.side_effect = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2,
                    "result": {"text": f"Document Title: 10_테마 금리\n\n{body}"}}),
    ]

    text = MCPGoogleDocsClient(mcp_cmd=["mock-node"]).read_text("doc-1")

    assert "Document Title" not in text
    assert text.startswith("# 10_테마 금리")
    assert "[r518]" in text


@patch("subprocess.Popen")
def test_read_text_keeps_body_when_no_preamble(mock_popen):
    mock_proc = MagicMock()
    mock_popen.return_value = mock_proc
    mock_proc.stdout.readline.side_effect = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"text": "■ [r1] 본문\n"}}),
    ]

    assert MCPGoogleDocsClient(mcp_cmd=["mock-node"]).read_text("doc-1") == "■ [r1] 본문\n"


def test_apply_sepia_theme_delegates_to_rest_applier(mock_subprocess):
    """MCP docs.formatText 로는 배경색을 못 바꾼다. REST 적용기에 위임해야 한다.

    과거 구현은 style:"sepia" 를 보내고 예외를 삼킨 뒤 무조건 True 를 반환했다.
    그 결과 이 경로로 만든 문서는 background 가 미설정 상태로 남았다.
    """
    client = MCPGoogleDocsClient(mcp_cmd=["mock-node", "mock-script.js"])
    fake = MagicMock()
    fake.apply.return_value = True
    client._theme_applier = fake

    assert client.apply_sepia_theme("doc-123") is True
    fake.apply.assert_called_once_with("doc-123")


def test_apply_sepia_theme_reports_real_failure(mock_subprocess):
    """실패를 True 로 감추지 않는다."""
    client = MCPGoogleDocsClient(mcp_cmd=["mock-node", "mock-script.js"])
    fake = MagicMock()
    fake.apply.return_value = False
    client._theme_applier = fake

    assert client.apply_sepia_theme("doc-123") is False

def test_mcp_client_connection_error(capsys):
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.side_effect = OSError("executable not found")
        client = MCPGoogleDocsClient(mcp_cmd=["invalid-node"])
        
        # append_text 내부에서 OSError를 잡아서 False를 반환함
        res = client.append_text("doc-1", "test")
        assert res is False
            
        captured = capsys.readouterr()
        assert "Error starting MCP server" in captured.err

def test_mcp_client_read_closed_stream_error(mock_subprocess):
    mock_popen, mock_proc = mock_subprocess
    mock_proc.stdout.readline.side_effect = [""] # closed stream
    
    client = MCPGoogleDocsClient(mcp_cmd=["mock-node"])
    # append_text 내부에서 RuntimeError를 잡아서 False를 반환함
    res = client.append_text("doc-1", "test")
    assert res is False

def test_mcp_client_response_rpc_error(mock_subprocess):
    mock_popen, mock_proc = mock_subprocess
    mock_proc.stdout.readline.side_effect = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}), # init ok
        json.dumps({"jsonrpc": "2.0", "id": 2, "error": {"code": -32602, "message": "Invalid params"}}) # RPC error
    ]
    
    client = MCPGoogleDocsClient(mcp_cmd=["mock-node"])
    # append_text 내부에서 RuntimeError를 잡아서 False를 반환함
    res = client.append_text("doc-1", "test")
    assert res is False

def test_mcp_client_append_text_exception_handling(mock_subprocess, capsys):
    mock_popen, mock_proc = mock_subprocess
    mock_proc.stdout.readline.side_effect = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}), # init ok
        RuntimeError("Network timeout") # mock send exception
    ]
    
    client = MCPGoogleDocsClient(mcp_cmd=["mock-node"])
    res = client.append_text("doc-1", "test")
    
    assert res is False
    captured = capsys.readouterr()
    assert "append_text failed" in captured.err

@patch("subprocess.Popen")
def test_mcp_client_read_text(mock_popen):
    mock_proc = MagicMock()
    mock_popen.return_value = mock_proc
    mock_proc.stdout.readline.side_effect = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}), # init ok
        json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"text": "Document full content text"}}) # getText ok
    ]
    
    client = MCPGoogleDocsClient(mcp_cmd=["mock-node"])
    text = client.read_text("doc-123")
    
    assert text == "Document full content text"
    written_data = "".join([call.args[0] for call in mock_proc.stdin.write.call_args_list])
    assert "docs.getText" in written_data
    assert "doc-123" in written_data

@patch("subprocess.Popen")
def test_mcp_client_overwrite_text_single_paragraph(mock_popen):
    """본문이 한 문단이면 replaceText 로 정확히 교체할 수 있다."""
    mock_proc = MagicMock()
    mock_popen.return_value = mock_proc
    mock_proc.stdout.readline.side_effect = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),                              # init
        json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"text": "Existing Content"}}),     # read_text
        json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"success": True}}),                # replaceText
        json.dumps({"jsonrpc": "2.0", "id": 4, "result": {"text": "New overwrite content"}}) # 검증 read_text
    ]

    client = MCPGoogleDocsClient(mcp_cmd=["mock-node"])
    res = client.overwrite_text("doc-123", "New overwrite content")

    assert res is True


@patch("subprocess.Popen")
def test_mcp_client_overwrite_text_refuses_multi_paragraph(mock_popen, capsys):
    """replaceAllText 는 문단 경계를 넘지 못하므로 여러 문단 문서는 치환할 수 없다.

    과거에는 이 경우에도 True 를 반환해 '조용한 성공'으로 위장됐다.
    """
    mock_proc = MagicMock()
    mock_popen.return_value = mock_proc
    mock_proc.stdout.readline.side_effect = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"text": "첫째 줄\n둘째 줄\n셋째 줄"}}),
    ]

    client = MCPGoogleDocsClient(mcp_cmd=["mock-node"])
    res = client.overwrite_text("doc-123", "새 본문")

    assert res is False
    assert "여러 문단 문서를 치환할 수 없습니다" in capsys.readouterr().err


@patch("subprocess.Popen")
def test_mcp_client_replace_line_verifies_result(mock_popen):
    mock_proc = MagicMock()
    mock_popen.return_value = mock_proc
    mock_proc.stdout.readline.side_effect = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),                    # init
        json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"success": True}}),     # replaceText
        json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"text": "▶ 💭 내 생각: 반영된 의견"}}),
    ]

    client = MCPGoogleDocsClient(mcp_cmd=["mock-node"])
    res = client.replace_line("doc-123", "▶ 💭 내 생각: 미기록 [r755]", "▶ 💭 내 생각: 반영된 의견")

    assert res is True
    written = "".join([call.args[0] for call in mock_proc.stdin.write.call_args_list])
    assert "docs.replaceText" in written


@patch("subprocess.Popen")
def test_mcp_client_replace_line_returns_false_when_target_remains(mock_popen, capsys):
    """치환이 반영되지 않았는데 성공으로 보고하면 사용자 생각이 유실된다."""
    mock_proc = MagicMock()
    mock_popen.return_value = mock_proc
    mock_proc.stdout.readline.side_effect = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"success": True}}),
        # 치환 대상이 그대로 남아 있음 (실제로는 아무것도 바뀌지 않았다)
        json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"text": "▶ 💭 내 생각: 미기록 [r755]"}}),
    ]

    client = MCPGoogleDocsClient(mcp_cmd=["mock-node"])
    res = client.replace_line("doc-123", "▶ 💭 내 생각: 미기록 [r755]", "▶ 💭 내 생각: 반영된 의견")

    assert res is False
    assert "대상 문자열이 그대로 남아 있습니다" in capsys.readouterr().err


