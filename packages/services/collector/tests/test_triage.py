from datetime import datetime, timezone

from agy_gateway import WorkerResult, WorkerRunner, WorkerSpec, WorkerUnavailableError
from collector.triage import ResearchTriage
from telegram_client import TelegramMessage


class ScriptedRunner(WorkerRunner):
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def run(self, spec, prompt, schema=None):
        self.prompts.append(prompt)
        if not self.responses:
            raise WorkerUnavailableError("응답 없음")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return WorkerResult(structured=nxt, worker=spec.name, model=spec.model)


def spec(batch_size=15, max_prompt_chars=11000):
    return WorkerSpec(
        name="triage",
        model="gemini-3.5-flash-medium",
        batch_size=batch_size,
        max_prompt_chars=max_prompt_chars,
    )


def msg(msg_id, text):
    return TelegramMessage(
        id=msg_id, channel_id="c", text=text, date=datetime.now(timezone.utc)
    )


def decisions(*pairs):
    return {
        "decisions": [
            {"message_id": mid, "is_research": flag, "reason": "판단"}
            for mid, flag in pairs
        ]
    }


def test_program_filter_handles_certain_cases_without_calling_worker():
    """확실한 비리서치는 워커를 호출하지 않는다 (토큰 0)."""
    runner = ScriptedRunner([])
    triage = ResearchTriage(runner, spec())

    out = triage.classify([
        msg(1, "[empty]"),
        msg(2, "https://only.link/post"),
        msg(3, "[공지] 이벤트 안내"),
    ])

    assert runner.prompts == []
    assert all(not d.is_research for d in out.values())
    assert out[1].reason == "본문 없음(미디어 전용)"
    assert out[2].reason == "링크만 있음"
    assert out[3].reason == "광고·공지 키워드"


def test_short_judgment_comment_reaches_worker_and_can_be_research():
    """짧은 판단 코멘트는 프로그램이 버리지 않고 워커가 판별한다."""
    runner = ScriptedRunner([decisions((10, True))])
    triage = ResearchTriage(runner, spec())

    out = triage.classify([msg(10, "코스닥 사이드카. 단기 과열권인듯 합니다")])

    assert out[10].is_research is True
    assert len(runner.prompts) == 1
    assert "message_id: 10" in runner.prompts[0]


def test_mixed_batch_splits_program_and_worker_decisions():
    runner = ScriptedRunner([decisions((20, True), (21, False))])
    triage = ResearchTriage(runner, spec())

    out = triage.classify([
        msg(19, "[empty]"),
        msg(20, "환율이 급등했다. 수출주 마진 압박 가능성."),
        msg(21, "ㅎㅎ 감사합니다"),
    ])

    assert out[19].is_research is False
    assert out[20].is_research is True
    assert out[21].is_research is False
    # 프로그램이 확정한 19번은 프롬프트에 포함되지 않아야 한다
    assert "message_id: 19" not in runner.prompts[0]


def test_batches_respect_batch_size():
    runner = ScriptedRunner([
        decisions((1, True), (2, True)),
        decisions((3, False), (4, True)),
    ])
    triage = ResearchTriage(runner, spec(batch_size=2))

    out = triage.classify([msg(i, f"판단 코멘트 {i}") for i in (1, 2, 3, 4)])

    assert len(runner.prompts) == 2
    assert set(out) == {1, 2, 3, 4}


def test_prompt_char_budget_is_respected():
    runner = ScriptedRunner([decisions((1, True)), decisions((2, True))])
    limit = 2000
    triage = ResearchTriage(runner, spec(batch_size=10, max_prompt_chars=limit),
                            max_preview_chars=800)

    triage.classify([msg(1, "가" * 900), msg(2, "나" * 900)])

    # batch_size 는 10 이지만 길이 예산 때문에 2회로 쪼개져야 한다
    assert len(runner.prompts) == 2
    for p in runner.prompts:
        assert len(p) <= limit


def test_worker_failure_defaults_to_include_so_posts_are_not_lost():
    """판별 실패 시 글을 버리지 않는다. 분석 단계에서 다시 검증된다."""
    runner = ScriptedRunner([WorkerUnavailableError("agy 실패")])
    triage = ResearchTriage(runner, spec(), include_on_failure=True)

    out = triage.classify([msg(30, "금리 인상 우려에 대한 판단")])

    assert out[30].is_research is True
    assert "보수적 기본값" in out[30].reason


def test_missing_decision_is_filled_conservatively():
    runner = ScriptedRunner([decisions((40, True))])
    triage = ResearchTriage(runner, spec())

    out = triage.classify([msg(40, "판단 A"), msg(41, "판단 B")])

    assert out[40].is_research is True
    assert out[41].is_research is True
    assert "누락" in out[41].reason


def test_unrequested_decision_is_ignored():
    runner = ScriptedRunner([decisions((50, True), (999, True))])
    triage = ResearchTriage(runner, spec())

    out = triage.classify([msg(50, "판단")])

    assert set(out) == {50}


def test_fallback_ids_track_posts_recorded_without_worker_verdict():
    """워커 실패로 기본값 처리된 글은 fallback_ids 에 남아, 배치가 알림에 실을 수 있어야 한다.

    2026-09-04 에 triage 워커가 실패해 후보 전원이 추출·기록됐는데 아무 알림이 없었다.
    """
    from collector.triage import REASON_FALLBACK

    runner = ScriptedRunner([WorkerUnavailableError("agy 실패")])
    triage = ResearchTriage(runner, spec())

    out = triage.classify([msg(30, "판단 A"), msg(31, "판단 B")])

    assert triage.fallback_ids == {30, 31}
    assert out[30].reason == REASON_FALLBACK


def test_fallback_ids_reset_between_runs():
    runner = ScriptedRunner([WorkerUnavailableError("agy 실패"), decisions((50, True))])
    triage = ResearchTriage(runner, spec())

    triage.classify([msg(40, "판단")])
    assert triage.fallback_ids == {40}

    triage.classify([msg(50, "판단")])
    assert triage.fallback_ids == set()
