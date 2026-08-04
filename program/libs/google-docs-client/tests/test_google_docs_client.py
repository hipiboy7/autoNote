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

def test_mcp_client_apply_sepia_theme(mock_subprocess):
    # sepia theme 및 batchUpdate 기능은 현행 API 로그 우회이므로 프로세스 통신 없이 즉각 True 반환
    client = MCPGoogleDocsClient(mcp_cmd=["mock-node", "mock-script.js"])
    assert client.apply_sepia_theme("doc-123") is True
    assert client.batch_update("doc-123", []) is True

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
def test_mcp_client_overwrite_text(mock_popen):
    mock_proc = MagicMock()
    mock_popen.return_value = mock_proc
    mock_proc.stdout.readline.side_effect = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}), # init ok
        json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"text": "Existing Content"}}), # read_text ok
        json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"success": True}}) # replaceText ok
    ]
    
    client = MCPGoogleDocsClient(mcp_cmd=["mock-node"])
    res = client.overwrite_text("doc-123", "New overwrite content")
    
    assert res is True


