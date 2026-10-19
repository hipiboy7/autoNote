from unittest.mock import MagicMock

import pytest
from agy_gateway import WorkerSpec, WorkerUnavailableError

from autonote.worker_roster import (
    BLOCKED_MODELS,
    DEFAULT_ROSTER,
    KNOWN_MODELS,
    RosterModelError,
    verify_roster_models,
)


def roster(**models):
    return {role: WorkerSpec(name=role, model=model) for role, model in models.items()}


def runner_with(models):
    runner = MagicMock()
    runner.list_models.return_value = models
    return runner


def test_default_roster_uses_only_known_and_allowed_models():
    for role, spec in DEFAULT_ROSTER.items():
        assert spec.model in KNOWN_MODELS, role
        assert spec.model not in BLOCKED_MODELS, role


def test_passes_when_every_role_model_is_available():
    verify_roster_models(
        roster(triage="a", extract="b"), runner_with(["a", "b", "c"])
    )


def test_missing_model_fails_with_role_model_and_alternatives():
    """2026-10-13~19: 로스터의 모델이 agy 에서 사라져 매일 실패했다.

    triage 는 조용히 폴백하고 review 는 stderr 한 줄로 끝나, 원인이 늦게 드러났다.
    배치 시작 전에 한 번에, 무엇을 바꿔야 하는지까지 알려야 한다.
    """
    with pytest.raises(RosterModelError) as exc:
        verify_roster_models(
            roster(triage="old-flash", extract="new-flash", review="old-opus"),
            runner_with(["new-flash", "new-opus"]),
        )

    message = str(exc.value)
    assert "triage=old-flash" in message
    assert "review=old-opus" in message
    assert "extract" not in message.split("사용 가능")[0]
    assert "new-opus" in message            # 대체 후보를 함께 보여준다
    assert "AGY_WORKER_" in message         # 즉시 우회하는 방법


def test_unknown_model_list_skips_the_check(capsys):
    """목록을 얻지 못하면 배치를 막지 않는다. 워커 호출 단계가 실제 오류를 보고한다."""
    runner = MagicMock()
    runner.list_models.side_effect = WorkerUnavailableError("network down")

    verify_roster_models(roster(triage="x"), runner)

    assert "network down" in capsys.readouterr().err


def test_runner_without_model_listing_skips_the_check():
    verify_roster_models(roster(triage="x"), runner_with(None))
