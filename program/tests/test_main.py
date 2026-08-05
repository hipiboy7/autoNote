import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from analyzer import ResearchReport
from collector.service import MessageCollector
from collector.triage import REASON_NO_BODY, TriageDecision
from merger.service import CommentMerger
from reviewer.service import WeeklyReviewer
from telegram_client import TelegramMessage
from writer.service import ResearchWriter

# main.py에서 노출할 오케스트레이터 배치 함수를 테스트
from main import parse_args, run_batch, verify_theme_mappings

CHANNEL = "-1004421517798"


def report(theme="반도체사이클"):
    return ResearchReport(
        main_claim="핵심 주장",
        evidence=["근거 1"],
        logic="논리",
        risks=["리스크 1"],
        critical_question="질문 1",
        theme=theme,
    )


@pytest.fixture
def mock_pipeline_services():
    state_manager = MagicMock()
    state_manager.load_state.return_value = MagicMock(
        last_message_id=100,
        last_weekly_review_week=None,
        channels={CHANNEL: MagicMock(last_message_id=100)},
    )

    collector = MagicMock(spec=MessageCollector)
    collector.state_manager = state_manager

    # 101: 본문 없는 미디어 전용, 102: 잡담, 103: 리서치
    msg1 = TelegramMessage(id=101, channel_id="c", text="[empty]", date=datetime.now(timezone.utc))
    msg2 = TelegramMessage(id=102, channel_id="c", text="ㅎㅎ 감사합니다", date=datetime.now(timezone.utc))
    msg3 = TelegramMessage(
        id=103, channel_id="c",
        text="반도체 HBM 분석\n공급 부족 지속\n판가 상승 전망",
        date=datetime.now(timezone.utc),
    )
    collector.collect_new_messages.return_value = [msg1, msg2, msg3]

    triage = MagicMock()
    triage.classify.return_value = {
        101: TriageDecision(message_id=101, is_research=False, reason=REASON_NO_BODY),
        102: TriageDecision(message_id=102, is_research=False, reason="잡담"),
        103: TriageDecision(message_id=103, is_research=True, reason="분석 포함"),
    }

    analyzer = MagicMock()
    analyzer.analyze_batch.return_value = {103: report()}

    writer = MagicMock(spec=ResearchWriter)
    merger = MagicMock(spec=CommentMerger)
    reviewer = MagicMock(spec=WeeklyReviewer)
    reviewer.generate_weekly_review.return_value = True

    return collector, triage, analyzer, writer, merger, reviewer, state_manager


DOC_MAPPINGS = {
    "원문보관": "orig_doc",
    "반도체사이클": "theme_doc",
    "review": "review_doc",
}


def test_main_runs_full_batch_pipeline(mock_pipeline_services):
    collector, triage, analyzer, writer, merger, reviewer, state_manager = mock_pipeline_services
    base_date = datetime(2026, 7, 18, 12, 0, 0)

    res = run_batch(
        collector=collector,
        triage=triage,
        analyzer=analyzer,
        writer=writer,
        merger=merger,
        reviewer=reviewer,
        thought_doc_id="thought_doc",
        original_doc_resolver=lambda msg_date: "orig_doc",
        doc_mappings=DOC_MAPPINGS,
        channel_id=CHANNEL,
        base_date=base_date,
    )

    assert res is True

    # 1. 어제 생각 병합이 먼저 구동
    merger.merge_thoughts.assert_called_once_with("thought_doc")

    # 2. 신규 메시지 수집
    collector.collect_new_messages.assert_called_once_with(CHANNEL)

    # 3. 판별은 한 번에 배치로 (건별 호출은 토큰 낭비)
    triage.classify.assert_called_once()

    # 4. 분석도 한 번에 배치로, 리서치 판정된 103번만 대상
    analyzer.analyze_batch.assert_called_once()
    assert [mid for mid, _ in analyzer.analyze_batch.call_args.args[0]] == [103]

    # 5. 본문 없는 101번은 원문보관에 기록하지 않는다 (보관할 원문이 없다)
    written_ids = [c.args[1].id for c in writer.write_original_backup.call_args_list]
    assert written_ids == [102, 103]

    # 6. 테마 문서 기록은 리서치 1건만
    writer.write_research_report.assert_called_once()
    assert writer.write_research_report.call_args.args[0] == "theme_doc"

    # 7. 생각노트 Pre-fill 도 본문 있는 2건만
    assert writer.pre_fill_thought_note.call_count == 2

    # 8. 주간리뷰 생성 및 상태 갱신
    reviewer.generate_weekly_review.assert_called_once_with("review_doc", base_date)
    state_manager.update_channel_state.assert_called_with(CHANNEL, 103)


def test_weekly_review_runs_even_when_no_new_messages(mock_pipeline_services):
    """조용한 월요일에도 주간리뷰가 생성되어야 한다.

    과거에는 '신규 없음' 조기 반환 뒤에 리뷰 단계가 있어 아예 실행되지 않았다.
    """
    collector, triage, analyzer, writer, merger, reviewer, state_manager = mock_pipeline_services
    collector.collect_new_messages.return_value = []
    base_date = datetime(2026, 7, 20, 9, 0, 0)

    res = run_batch(
        collector=collector,
        triage=triage,
        analyzer=analyzer,
        writer=writer,
        merger=merger,
        reviewer=reviewer,
        thought_doc_id="thought_doc",
        original_doc_resolver=lambda msg_date: "orig_doc",
        doc_mappings=DOC_MAPPINGS,
        channel_id=CHANNEL,
        base_date=base_date,
    )

    assert res is True
    reviewer.generate_weekly_review.assert_called_once_with("review_doc", base_date)
    analyzer.analyze_batch.assert_not_called()


def test_weekly_review_week_not_recorded_when_generation_fails(mock_pipeline_services):
    """리뷰 생성이 실패하면 주차를 기록하지 않아 다음 배치에서 재시도된다."""
    collector, triage, analyzer, writer, merger, reviewer, state_manager = mock_pipeline_services
    collector.collect_new_messages.return_value = []
    reviewer.generate_weekly_review.return_value = False

    run_batch(
        collector=collector,
        triage=triage,
        analyzer=analyzer,
        writer=writer,
        merger=merger,
        reviewer=reviewer,
        thought_doc_id="thought_doc",
        original_doc_resolver=lambda msg_date: "orig_doc",
        doc_mappings=DOC_MAPPINGS,
        channel_id=CHANNEL,
        base_date=datetime(2026, 7, 20, 9, 0, 0),
    )

    state_manager.update_weekly_review_week.assert_not_called()


def test_main_partial_state_update_on_failure(mock_pipeline_services):
    """중단 시 직전 성공 ID까지만 저장하고, 실패한 글은 기록하지 않는다."""
    collector, triage, analyzer, writer, merger, reviewer, state_manager = mock_pipeline_services

    # 103번 기록 단계에서 강제 실패
    def fail_on_103(doc_id, msg, title):
        if msg.id == 103:
            raise RuntimeError("Docs write failed")
        return True

    writer.write_original_backup.side_effect = fail_on_103

    with pytest.raises(RuntimeError) as exc_info:
        run_batch(
            collector=collector,
            triage=triage,
            analyzer=analyzer,
            writer=writer,
            merger=merger,
            reviewer=reviewer,
            thought_doc_id="thought_doc",
            original_doc_resolver=lambda msg_date: "orig_doc",
            doc_mappings=DOC_MAPPINGS,
            channel_id=CHANNEL,
            base_date=datetime(2026, 7, 18, 12, 0, 0),
        )

    assert "Docs write failed" in str(exc_info.value)
    # 102번까지 성공했으므로 체크포인트는 102
    state_manager.update_channel_state.assert_called_with(CHANNEL, 102)


def test_unmapped_theme_raises_instead_of_silently_defaulting(mock_pipeline_services):
    """테마 문서 매핑이 없으면 조용히 기본 문서로 보내지 않고 중단한다."""
    collector, triage, analyzer, writer, merger, reviewer, state_manager = mock_pipeline_services
    analyzer.analyze_batch.return_value = {103: report(theme="금리")}

    with pytest.raises(KeyError):
        run_batch(
            collector=collector,
            triage=triage,
            analyzer=analyzer,
            writer=writer,
            merger=merger,
            reviewer=reviewer,
            thought_doc_id="thought_doc",
            original_doc_resolver=lambda msg_date: "orig_doc",
            doc_mappings=DOC_MAPPINGS,  # '금리' 매핑 없음
            channel_id=CHANNEL,
            base_date=datetime(2026, 7, 18, 12, 0, 0),
        )

    writer.write_research_report.assert_not_called()


def test_verify_theme_mappings_exits_when_incomplete(capsys):
    with pytest.raises(SystemExit):
        verify_theme_mappings({"반도체사이클": "doc"})

    assert "문서 매핑이 없는 테마" in capsys.readouterr().err


def test_verify_theme_mappings_passes_when_complete():
    from analyzer import THEMES

    verify_theme_mappings({theme: f"doc_{i}" for i, theme in enumerate(THEMES)})


def test_cli_argument_parsing():
    with patch.object(sys, "argv", ["main.py", "--mode", "merge",
                                   "--model", "claude-opus-4-6-thinking",
                                   "--channel", "c123"]):
        args = parse_args()
        assert args.mode == "merge"
        assert args.model == "claude-opus-4-6-thinking"
        assert args.channel == "c123"
