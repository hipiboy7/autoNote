import json
from unittest.mock import MagicMock, patch

import pytest

from agy_gateway.agy_cli import AgyCliRunner
from agy_gateway.interface import WorkerSpec, WorkerUnavailableError

SCHEMA = {
    "type": "object",
    "properties": {"theme": {"type": "string", "enum": ["금리", "반도체사이클"]}},
    "required": ["theme"],
}


def envelope(**overrides):
    base = {
        "conversation_id": "abc",
        "status": "SUCCESS",
        "response": '{"theme":"금리"}',
        "duration_seconds": 22.7,
        "num_turns": 1,
        "structured_output": {"theme": "금리"},
        "usage": {"input_tokens": 20695, "output_tokens": 1510, "total_tokens": 22205},
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


def spec(**kwargs):
    defaults = dict(name="extract", model="gemini-3.1-pro-high", timeout_seconds=60)
    defaults.update(kwargs)
    return WorkerSpec(**defaults)


@patch("agy_gateway.agy_cli.subprocess.run")
def test_run_returns_structured_output_and_usage(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0, stdout=envelope(), stderr="")

    runner = AgyCliRunner(agy_path="agy.exe", usage_log_path=str(tmp_path / "usage.jsonl"))
    result = runner.run(spec(), "분석해라", SCHEMA)

    assert result.structured == {"theme": "금리"}
    assert result.total_tokens == 22205
    assert result.model == "gemini-3.1-pro-high"
    assert result.worker == "extract"

    # 사용량이 JSONL 로 기록되어야 한다 (토큰 최적화 효과 측정 근거)
    logged = (tmp_path / "usage.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(logged)["total_tokens"] == 22205


@patch("agy_gateway.agy_cli.subprocess.run")
def test_json_schema_requires_json_output_format(mock_run):
    """--json-schema 는 --output-format json 과 함께만 허용된다 (실측 확인)."""
    mock_run.return_value = MagicMock(returncode=0, stdout=envelope(), stderr="")

    runner = AgyCliRunner(agy_path="agy.exe", usage_log_path="")
    runner.run(spec(effort="high"), "분석해라", SCHEMA)

    cmd = mock_run.call_args.args[0]
    assert "--output-format" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert "--json-schema" in cmd
    assert cmd[cmd.index("--effort") + 1] == "high"
    assert "--dangerously-skip-permissions" in cmd


@patch("agy_gateway.agy_cli.subprocess.run")
def test_recovers_json_when_model_prepends_prose(mock_run):
    """일부 모델은 산문을 덧붙인다. structured_output 이 없으면 본문에서 회수한다."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=envelope(
            structured_output=None,
            response='분석 결과를 정리했습니다.\n{"theme":"반도체사이클"}\n',
        ),
        stderr="",
    )

    runner = AgyCliRunner(agy_path="agy.exe", usage_log_path="")
    result = runner.run(spec(), "분석해라", SCHEMA)

    assert result.structured == {"theme": "반도체사이클"}


@patch("agy_gateway.agy_cli.subprocess.run")
def test_raises_when_structured_output_unobtainable(mock_run):
    """스키마를 요구했는데 구조화 출력이 없으면 창작하지 않고 예외로 중단한다."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=envelope(structured_output=None, response="죄송하지만 분석할 수 없습니다."),
        stderr="",
    )

    runner = AgyCliRunner(agy_path="agy.exe", usage_log_path="", max_attempts=1)
    with pytest.raises(WorkerUnavailableError):
        runner.run(spec(), "분석해라", SCHEMA)


@patch("agy_gateway.agy_cli.subprocess.run")
def test_retries_then_raises_on_nonzero_exit(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error: bad model")

    runner = AgyCliRunner(agy_path="agy.exe", usage_log_path="", max_attempts=3)
    with pytest.raises(WorkerUnavailableError) as exc:
        runner.run(spec(), "분석해라", SCHEMA)

    assert mock_run.call_count == 3
    assert "3회 모두 실패" in str(exc.value)


@patch("agy_gateway.agy_cli.subprocess.run")
def test_non_success_status_raises_when_output_unrecoverable(mock_run):
    """status=ERROR 자체로는 실패가 아니다. 구조화 출력까지 못 얻을 때 실패다.

    agy 1.1.14 부터 status=ERROR 는 '내부 재시도가 있었다' 는 뜻일 수도 있어서,
    판정 기준은 structured_output 확보 여부다. error 내용은 메시지에 실어 보낸다.
    """
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=envelope(
            status="ERROR",
            structured_output=None,
            response="죄송하지만 분석할 수 없습니다.",
            error="invalid arguments: at '/reports'",
        ),
        stderr="",
    )

    runner = AgyCliRunner(agy_path="agy.exe", usage_log_path="", max_attempts=1)
    with pytest.raises(WorkerUnavailableError) as exc:
        runner.run(spec(), "분석해라", SCHEMA)

    assert "invalid arguments" in str(exc.value)


@patch("agy_gateway.agy_cli.subprocess.run")
def test_status_error_is_accepted_when_structured_output_is_valid(mock_run):
    """agy 1.1.14: 내부 재시도로 스키마를 맞춘 뒤에도 status=ERROR 가 남는다.

    structured_output 이 멀쩡한데 status 만 보고 버리면 결과를 잃는다.
    2026-08-15~18 배치가 이것 때문에 나흘간 실패했다.
    """
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=envelope(
            status="ERROR",
            error="invalid arguments:\n- at '/reports/0': missing properties 'main_claim'",
        ),
        stderr="",
    )

    runner = AgyCliRunner(agy_path="agy.exe", usage_log_path="", max_attempts=1)
    result = runner.run(spec(), "분석해라", SCHEMA)

    assert result.structured == {"theme": "금리"}
    assert result.total_tokens == 22205


@patch("agy_gateway.agy_cli.subprocess.run")
def test_status_error_without_schema_still_raises(mock_run):
    """스키마가 없으면 유효성을 판정할 근거가 없으므로 status 를 그대로 신뢰한다."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=envelope(status="ERROR", structured_output=None, error="boom"),
        stderr="",
    )

    runner = AgyCliRunner(agy_path="agy.exe", usage_log_path="", max_attempts=1)
    with pytest.raises(WorkerUnavailableError) as exc:
        runner.run(spec(), "분석해라", schema=None)

    assert "boom" in str(exc.value)


def test_oversized_prompt_is_rejected_before_launching_agy():
    """Windows 명령행 한계를 넘기기 전에 프로그램이 먼저 막는다."""
    runner = AgyCliRunner(agy_path="agy.exe", usage_log_path="")
    with pytest.raises(WorkerUnavailableError) as exc:
        runner.run(spec(max_prompt_chars=100), "x" * 500, SCHEMA)

    assert "배치 크기를 줄이십시오" in str(exc.value)


@patch("agy_gateway.agy_cli.subprocess.run")
def test_nonzero_exit_surfaces_error_from_stdout_envelope(mock_run):
    """agy 는 실패 원인을 stderr 가 아니라 stdout 봉투에 적는다.

    주간리뷰가 3주간 실패하는 동안 진짜 원인이 내내 stdout 에 있었는데
    stderr 만 기록해 `exit=1 stderr=` 만 남았다 (2026-08-26 확인).
    """
    mock_run.return_value = MagicMock(
        returncode=1,
        stdout=json.dumps({
            "conversation_id": "abc",
            "status": "ERROR",
            "response": "",
            "error": "timeout waiting for response",
            "duration_seconds": 228.5,
        }),
        stderr="",
    )

    runner = AgyCliRunner(agy_path="agy.exe", usage_log_path="", max_attempts=1)
    with pytest.raises(WorkerUnavailableError) as exc:
        runner.run(spec(), "분석해라", SCHEMA)

    message = str(exc.value)
    assert "timeout waiting for response" in message
    assert "status=ERROR" in message
    assert "stderr=(없음)" in message


@patch("agy_gateway.agy_cli.subprocess.run")
def test_nonzero_exit_includes_raw_stdout_when_not_json(mock_run):
    """봉투를 못 읽어도 stdout 을 버리지 않는다."""
    mock_run.return_value = MagicMock(
        returncode=2, stdout="Fatal: model not available", stderr=""
    )

    runner = AgyCliRunner(agy_path="agy.exe", usage_log_path="", max_attempts=1)
    with pytest.raises(WorkerUnavailableError) as exc:
        runner.run(spec(), "분석해라", SCHEMA)

    assert "Fatal: model not available" in str(exc.value)
    assert "exit=2" in str(exc.value)


@patch("agy_gateway.agy_cli.subprocess.run")
def test_error_message_is_truncated(mock_run):
    """거대한 프롬프트가 오류 로그를 덮지 않아야 한다."""
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="E" * 5000)

    runner = AgyCliRunner(agy_path="agy.exe", usage_log_path="", max_attempts=1)
    with pytest.raises(WorkerUnavailableError) as exc:
        runner.run(spec(), "분석해라", SCHEMA)

    assert len(str(exc.value)) < 800
    assert "이하 생략" in str(exc.value)


@patch("agy_gateway.agy_cli.subprocess.run")
def test_large_schema_is_passed_as_file(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout=envelope(), stderr="")

    big_schema = {
        "type": "object",
        "properties": {f"f{i}": {"type": "string", "description": "x" * 60} for i in range(80)},
    }
    runner = AgyCliRunner(agy_path="agy.exe", usage_log_path="")
    runner.run(spec(), "분석해라", big_schema)

    cmd = mock_run.call_args.args[0]
    schema_arg = cmd[cmd.index("--json-schema") + 1]
    assert schema_arg.endswith(".schema.json")
