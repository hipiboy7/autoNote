import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from google_docs_client.docs_style import DEFAULT_BG, DEFAULT_FG, SepiaThemeApplier
from google_docs_client.google_auth import GoogleAuthError, GoogleTokenProvider


class FakeProvider:
    def __init__(self, token="tok-123", raises=False):
        self.token = token
        self.raises = raises

    def get_token(self):
        if self.raises:
            raise GoogleAuthError("만료")
        return self.token


def http_response(payload):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda s, *a: False
    return resp


def sent_payloads(mock_urlopen):
    """urlopen 에 전달된 요청들의 (url, method, body) 를 뽑는다."""
    out = []
    for call in mock_urlopen.call_args_list:
        req = call.args[0]
        body = None
        if req.data:
            body = json.loads(req.data.decode("utf-8"))
        out.append((req.full_url, req.get_method(), body))
    return out


# ------------------------------------------------------------------ 적용

@patch("google_docs_client.docs_rest.urllib.request.urlopen")
def test_apply_sets_background_and_foreground(mock_urlopen):
    mock_urlopen.side_effect = [
        http_response({"body": {"content": [{"endIndex": 500}]}}),   # 구조 조회
        http_response({"replies": []}),                              # batchUpdate
    ]

    ok = SepiaThemeApplier(FakeProvider()).apply("doc-1")

    assert ok is True
    calls = sent_payloads(mock_urlopen)
    assert "documents/doc-1" in calls[0][0]
    assert calls[1][0].endswith("doc-1:batchUpdate")

    requests = calls[1][2]["requests"]
    doc_style = requests[0]["updateDocumentStyle"]
    rgb = doc_style["documentStyle"]["background"]["color"]["color"]["rgbColor"]
    assert (rgb["red"], rgb["green"], rgb["blue"]) == DEFAULT_BG
    assert doc_style["fields"] == "background"

    text_style = requests[1]["updateTextStyle"]
    # foregroundColor 는 OptionalColor 자체다. 중첩을 한 단계 더 하면 400 이 난다.
    fg = text_style["textStyle"]["foregroundColor"]["color"]["rgbColor"]
    assert (fg["red"], fg["green"], fg["blue"]) == DEFAULT_FG
    # 본문 전체 범위에 글자색을 적용해야 한다
    assert text_style["range"] == {"startIndex": 1, "endIndex": 499}


@patch("google_docs_client.docs_rest.urllib.request.urlopen")
def test_empty_document_only_gets_background(mock_urlopen):
    """본문이 비면 적용할 텍스트 범위가 없다. 배경만 칠한다."""
    mock_urlopen.side_effect = [
        http_response({"body": {"content": [{"endIndex": 1}]}}),
        http_response({}),
    ]

    assert SepiaThemeApplier(FakeProvider()).apply("doc-1") is True

    requests = sent_payloads(mock_urlopen)[1][2]["requests"]
    assert len(requests) == 1
    assert "updateDocumentStyle" in requests[0]


@patch("google_docs_client.docs_rest.urllib.request.urlopen")
def test_rgb_is_configurable(mock_urlopen, monkeypatch):
    """색상값을 코드에 박지 않는다 (gemini.md 룰 7)."""
    monkeypatch.setenv("DOC_THEME_BACKGROUND_RGB", "0.5,0.6,0.7")
    mock_urlopen.side_effect = [
        http_response({"body": {"content": [{"endIndex": 100}]}}),
        http_response({}),
    ]

    SepiaThemeApplier(FakeProvider()).apply("doc-1")

    requests = sent_payloads(mock_urlopen)[1][2]["requests"]
    rgb = requests[0]["updateDocumentStyle"]["documentStyle"]["background"]["color"]["color"]["rgbColor"]
    assert (rgb["red"], rgb["green"], rgb["blue"]) == (0.5, 0.6, 0.7)


@patch("google_docs_client.docs_rest.urllib.request.urlopen")
def test_malformed_rgb_env_falls_back(mock_urlopen, monkeypatch, capsys):
    monkeypatch.setenv("DOC_THEME_BACKGROUND_RGB", "빨강,초록")
    mock_urlopen.side_effect = [
        http_response({"body": {"content": [{"endIndex": 100}]}}),
        http_response({}),
    ]

    SepiaThemeApplier(FakeProvider()).apply("doc-1")

    assert "기본값" in capsys.readouterr().err


# ------------------------------------------------------------------ 실패 처리

def test_token_failure_returns_false(capsys):
    """토큰을 못 얻으면 실패로 보고한다 (True 로 감추지 않는다)."""
    assert SepiaThemeApplier(FakeProvider(raises=True)).apply("doc-1") is False
    err = capsys.readouterr().err
    assert "실패" in err


@patch("google_docs_client.docs_rest.urllib.request.urlopen")
def test_http_error_returns_false(mock_urlopen, capsys):
    err = urllib.error.HTTPError("url", 403, "Forbidden", {}, None)
    err.read = lambda: b'{"error":"insufficient permission"}'
    mock_urlopen.side_effect = [
        http_response({"body": {"content": [{"endIndex": 100}]}}),
        err,
    ]

    assert SepiaThemeApplier(FakeProvider()).apply("doc-1") is False
    captured = capsys.readouterr().err
    assert "테마 적용 실패" in captured
    assert "403" in captured


@patch("google_docs_client.docs_rest.urllib.request.urlopen")
def test_structure_lookup_failure_still_applies_background(mock_urlopen):
    """구조 조회가 실패해도 배경색은 칠한다 (부분 성공이 전무보다 낫다)."""
    mock_urlopen.side_effect = [OSError("네트워크 오류"), http_response({})]

    assert SepiaThemeApplier(FakeProvider()).apply("doc-1") is True
    requests = sent_payloads(mock_urlopen)[1][2]["requests"]
    assert len(requests) == 1


# ------------------------------------------------------------------ 검증

@patch("google_docs_client.docs_rest.urllib.request.urlopen")
def test_verify_returns_background_rgb(mock_urlopen):
    mock_urlopen.return_value = http_response({
        "documentStyle": {"background": {"color": {"color": {"rgbColor": {
            "red": 0.99, "green": 0.96, "blue": 0.89}}}}}
    })

    assert SepiaThemeApplier(FakeProvider()).verify("doc-1") == (0.99, 0.96, 0.89)


@patch("google_docs_client.docs_rest.urllib.request.urlopen")
def test_verify_returns_none_when_unset(mock_urlopen):
    mock_urlopen.return_value = http_response({"documentStyle": {}})
    assert SepiaThemeApplier(FakeProvider()).verify("doc-1") is None


# ------------------------------------------------------------------ 토큰 공급

def run_node(stdout, returncode=0):
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = ""
    proc.returncode = returncode
    return proc


def token_blob(expires_at, access="tok-abc"):
    return json.dumps({"main-account": {"token": {
        "accessToken": access, "refreshToken": "r", "expiresAt": expires_at}}})


@patch("google_docs_client.google_auth.subprocess.run")
@patch("google_docs_client.google_auth.time.time")
def test_valid_token_is_used_without_refresh(mock_time, mock_run):
    mock_time.return_value = 1000
    mock_run.return_value = run_node(token_blob(expires_at=(1000 + 3600) * 1000))
    refresh = MagicMock()

    provider = GoogleTokenProvider(refresh_callback=refresh)
    assert provider.get_token() == "tok-abc"
    refresh.assert_not_called()


@patch("google_docs_client.google_auth.subprocess.run")
@patch("google_docs_client.google_auth.time.time")
def test_expiring_token_triggers_refresh_then_reread(mock_time, mock_run, capsys):
    mock_time.return_value = 1000
    mock_run.side_effect = [
        run_node(token_blob(expires_at=1000 * 1000)),                       # 만료
        run_node(token_blob(expires_at=(1000 + 3600) * 1000, access="new")),  # 갱신 후
    ]
    refresh = MagicMock()

    provider = GoogleTokenProvider(refresh_callback=refresh)
    assert provider.get_token() == "new"
    refresh.assert_called_once()
    assert "갱신합니다" in capsys.readouterr().err


@patch("google_docs_client.google_auth.subprocess.run")
@patch("google_docs_client.google_auth.time.time")
def test_expired_without_refresh_callback_raises(mock_time, mock_run):
    mock_time.return_value = 1000
    mock_run.return_value = run_node(token_blob(expires_at=1000 * 1000))

    with pytest.raises(GoogleAuthError) as exc:
        GoogleTokenProvider(refresh_callback=None).get_token()
    assert "갱신 수단이 없습니다" in str(exc.value)


@patch("google_docs_client.google_auth.subprocess.run")
def test_decrypt_failure_raises(mock_run):
    mock_run.return_value = run_node("", returncode=1)
    with pytest.raises(GoogleAuthError):
        GoogleTokenProvider().get_token()


@patch("google_docs_client.google_auth.subprocess.run")
@patch("google_docs_client.google_auth.time.time")
def test_still_expired_after_refresh_raises(mock_time, mock_run):
    mock_time.return_value = 1000
    mock_run.return_value = run_node(token_blob(expires_at=1000 * 1000))

    with pytest.raises(GoogleAuthError) as exc:
        GoogleTokenProvider(refresh_callback=MagicMock()).get_token()
    assert "갱신 후에도" in str(exc.value)
