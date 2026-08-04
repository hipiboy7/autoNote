import sys
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from telegram_client import TelegramMessage
from collector.service import MessageCollector, StateManager
from analyzer import ResearchReport
from writer.service import ResearchWriter
from merger.service import CommentMerger
from reviewer.service import WeeklyReviewer

# main.py에서 노출할 오케스트레이터 배치 함수를 테스트
from main import run_batch, parse_args

@pytest.fixture
def mock_pipeline_services():
    # 1. StateManager & MessageCollector Mock
    state_manager = MagicMock()
    state_manager.load_state.return_value = MagicMock(
        last_message_id=100,
        channels={"-1004421517798": MagicMock(last_message_id=100)}
    )
    
    collector = MagicMock(spec=MessageCollector)
    collector.state_manager = state_manager
    # 수집 메시지 2건 모사
    msg1 = TelegramMessage(id=101, channel_id="c", text="단순 잡담 3줄 이하", date=datetime.now(timezone.utc))
    msg2 = TelegramMessage(id=102, channel_id="c", text="반도체 HBM 분석\n라인 2\n라인 3\n라인 4", date=datetime.now(timezone.utc))
    collector.collect_new_messages.return_value = [msg1, msg2]
    # msg1은 잡담(False), msg2는 리서치(True)
    collector.is_research.side_effect = lambda m: m.id == 102

    # 2. Analyzer Mock
    analyzer = MagicMock()
    analyzer.analyze_message.return_value = ResearchReport(
        main_claim="핵심 주장",
        evidence=["근거 1"],
        logic="논리",
        risks=["리스크 1"],
        critical_question="질문 1",
        theme="반도체사이클"
    )

    # 3. Writer Mock
    writer = MagicMock(spec=ResearchWriter)

    # 4. Merger Mock
    merger = MagicMock(spec=CommentMerger)

    # 5. Reviewer Mock
    reviewer = MagicMock(spec=WeeklyReviewer)

    return collector, analyzer, writer, merger, reviewer, state_manager

def test_main_runs_full_batch_pipeline(mock_pipeline_services):
    collector, analyzer, writer, merger, reviewer, state_manager = mock_pipeline_services
    
    doc_mappings = {
        "원문보관": "orig_doc",
        "반도체사이클": "theme_doc",
        "review": "review_doc"
    }

    # 토요일 기준 시점 제공 (리뷰 기동시키기 위함, 2026-07-18 토요일)
    base_date = datetime(2026, 7, 18, 12, 0, 0)

    # 배치를 구동함
    res = run_batch(
        collector=collector,
        analyzer=analyzer,
        writer=writer,
        merger=merger,
        reviewer=reviewer,
        thought_doc_id="thought_doc",
        original_doc_id="orig_doc",
        doc_mappings=doc_mappings,
        channel_id="-1004421517798",
        base_date=base_date
    )

    assert res is True

    # 1. 어제 생각 병합이 가장 먼저 구동되었는지 검증
    merger.merge_thoughts.assert_called_once_with("thought_doc")

    # 2. 신규 메시지 수집이 일어났는지 검증
    collector.collect_new_messages.assert_called_once_with("-1004421517798")

    # 3. 수집된 2개 글 모두 원문 보관 Docs에 추가 기록되었는지 검증
    assert writer.write_original_backup.call_count == 2

    # 4. 리서치 선별이 되었는지 검증
    assert collector.is_research.call_count == 2

    # 5. 리서치 판단된 102번 글만 분석 및 기록/생각 Pre-fill이 수행되었는지 검증
    analyzer.analyze_message.assert_called_once_with("반도체 HBM 분석\n라인 2\n라인 3\n라인 4")
    writer.write_research_report.assert_called_once()
    # 모든 메시지에 대해 pre_fill이 호출되므로 call_count == 2
    assert writer.pre_fill_thought_note.call_count == 2

    # 6. 토요일이므로 주간리뷰 생성이 가동되었는지 검증
    reviewer.generate_weekly_review.assert_called_once_with("review_doc", base_date)

    # 7. 상태 파일 last_message_id가 102(최종 성공 ID)로 안전 갱신되었는지 검증
    state_manager.update_channel_state.assert_called_with("-1004421517798", 102)

def test_main_partial_state_update_on_failure(mock_pipeline_services):
    collector, analyzer, writer, merger, reviewer, state_manager = mock_pipeline_services
    
    # 102번 분석 시 강제 크래시 유발
    analyzer.analyze_message.side_effect = RuntimeError("LLM API Fail")

    doc_mappings = {"r": "orig_doc", "t": "theme_doc"}
    base_date = datetime(2026, 7, 18, 12, 0, 0)

    # 배치를 구동함 (101번은 통과, 102번에서 에러)
    with pytest.raises(RuntimeError) as exc_info:
        run_batch(
            collector=collector,
            analyzer=analyzer,
            writer=writer,
            merger=merger,
            reviewer=reviewer,
            thought_doc_id="thought_doc",
            original_doc_id="orig_doc",
            doc_mappings=doc_mappings,
            channel_id="-1004421517798",
            base_date=base_date
        )

    assert "LLM API Fail" in str(exc_info.value)

    # 101번은 리서치가 아니므로 통과했고, last_success_id가 101로 업데이트되어 안전 세이브가 호출되었는지 검증
    state_manager.update_channel_state.assert_called_with("-1004421517798", 101)

def test_cli_argument_parsing():
    # 파라미터 분기 검증
    with patch.object(sys, 'argv', ['main.py', '--mode', 'merge', '--model', 'o4-mini', '--channel', 'c123']):
        args = parse_args()
        assert args.mode == "merge"
        assert args.model == "o4-mini"
        assert args.channel == "c123"
