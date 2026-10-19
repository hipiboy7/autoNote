import os
import sys
import argparse
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

# 라이브러리 및 서비스 모듈 import
from agy_gateway import AgyCliRunner
from telegram_client.mcp_client import MCPTelegramClient
from google_docs_client.mcp_client import MCPGoogleDocsClient

from collector.service import MessageCollector, StateManager
from collector.triage import REASON_NO_BODY, ResearchTriage
from analyzer.service import THEMES, ResearchAnalyzer
from writer.service import ResearchWriter
from merger.service import CommentMerger
from reviewer.service import WeeklyReviewer

from .batch_health import VERDICT_OK, evaluate_batch_health
from .batch_lock import require_lock
from .notify import AlertContext, BatchNotifier
from .original_docs import OriginalDocRegistry, parse_monthly_mapping
from .repair_docs import DocRepairer
from .restore_docs import DocRestorer
from .worker_roster import load_roster, verify_roster_models
from writer import local_now

def parse_args():
    parser = argparse.ArgumentParser(description="autoNote 투자 리서치 하이브리드 배치 CLI")
    parser.add_argument(
        "--model",
        type=str,
        default=os.getenv("AGY_REVIEW_MODEL", ""),
        help="주간리뷰 워커 모델 재지정 (agy models 기준, 미지정 시 로스터 기본값)"
    )
    parser.add_argument(
        "--channel",
        type=str,
        default=os.getenv("TELEGRAM_CHANNEL_ID", "-1004421517798"),
        help="수집할 텔레그램 채널 ID"
    )
    parser.add_argument(
        "--mode", 
        type=str, 
        choices=["batch", "merge", "review", "status", "backfill", "repair", "restore"],
        default="batch",
        help="배치 실행 모드 지정"
    )
    parser.add_argument(
        "--doc",
        type=str,
        default=None,
        help="복구 대상 문서 키 (repair 모드 전용, 예: 반도체사이클). all 이면 전체"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="repair 모드에서 오염 블록만 집계하고 문서를 만들지 않는다"
    )
    parser.add_argument(
        "--from-id",
        type=int,
        default=None,
        help="백필 시작 메시지 ID (backfill 모드 전용)"
    )
    parser.add_argument(
        "--to-id",
        type=int,
        default=None,
        help="백필 종료 메시지 ID (backfill 모드 전용)"
    )
    return parser.parse_args()

def resolve_original_doc(msg_date: datetime, original_doc_mappings: Dict[str, str], fallback_doc_id: str) -> str:
    """월별 원문보관 문서 ID를 반환한다 (매핑 dict 만 사용하는 단순 조회).

    미등록 월에 대해 fallback 으로 넘어가면 엉뚱한 달의 문서에 기록되므로,
    배치 경로에서는 OriginalDocRegistry 를 사용한다. 이 함수는 매핑이 이미
    확정된 경우에만 쓴다.
    """
    month_key = msg_date.strftime("%Y-%m")
    return original_doc_mappings.get(month_key, fallback_doc_id)

def triage_fallback_ids(triage: Any) -> List[int]:
    """triage 워커 판별 없이 기본값으로 처리된 message_id 목록 (없으면 빈 리스트).

    테스트의 MagicMock 같은 대체 객체도 들어오므로 실제 집합/리스트일 때만 읽는다.
    """
    ids = getattr(triage, "fallback_ids", None)
    if isinstance(ids, (set, frozenset, list, tuple)):
        return sorted(int(i) for i in ids)
    return []


def report_batch_success(
    notifier: BatchNotifier,
    state_manager: Any,
    previous_result: Optional[str],
    fallback_ids: Sequence[int],
    now: datetime,
) -> None:
    """성공 배치 뒤의 알림을 결정한다.

    평소 성공은 조용히 지나간다 — 매번 알리면 곧 무시된다. 알리는 경우는 둘이다.
      * 직전 실행이 실패였다             → 복구 알림 (기존)
      * triage 폴백으로 기록된 글이 있다  → 주의 알림. 검증 없이 기록된 글이 있다는 뜻이다.
    둘이 겹치면 복구 알림 한 통에 폴백 내용을 함께 싣는다.

    2026-09-04 에 triage 워커가 실패해 후보 전원이 추출·기록됐는데 아무 알림이 없었다.
    """
    fallback_note = ""
    if fallback_ids:
        ids = ", ".join(f"r{i}" for i in fallback_ids)
        fallback_note = (
            f"판별(triage) 워커가 실패해 {len(fallback_ids)}건이 검증 없이 리서치로 "
            f"간주되어 추출·기록되었습니다: {ids}"
        )

    state = state_manager.load_state()
    context = AlertContext(
        mode="batch",
        when=now,
        last_message_id=state.last_message_id,
        hint=(
            "폴백된 글이 잡담·링크였다면 테마 문서에 잘못 섞였을 수 있습니다. "
            "문서에서 위 식별자를 확인하십시오."
        ) if fallback_ids else "",
    )

    if previous_result == "fail":
        summary = "이전 실행 실패 후 이번 배치는 정상 완료되었습니다."
        if fallback_note:
            summary += "\n\n" + fallback_note
        notifier.notify_recovery(summary=summary, context=context)
    elif fallback_ids:
        notifier.notify_warning(
            summary="일일 배치는 정상 완료했지만 판별 단계가 폴백으로 처리된 글이 있습니다.",
            detail=fallback_note,
            context=context,
        )


def run_weekly_review_if_due(
    collector: MessageCollector,
    reviewer: WeeklyReviewer,
    doc_mappings: Dict[str, str],
    base_date: datetime,
) -> bool:
    """그 주(ISO Week)의 첫 배치라면 지난주 주간리뷰를 생성한다.

    신규 메시지가 없는 날에도 실행되어야 한다. 과거에는 '신규 없음' 조기 반환 뒤에
    리뷰 단계가 놓여 있어, 조용한 월요일에는 주간리뷰가 아예 생성되지 않았다.
    """
    current_iso_week = base_date.strftime("%G-W%V")
    state = collector.state_manager.load_state()
    if state.last_weekly_review_week == current_iso_week:
        return False

    review_doc_id = doc_mappings.get("review")
    if not review_doc_id:
        print("[Batch] 리뷰 문서 매핑이 없어 주간리뷰를 건너뜁니다.", file=sys.stderr)
        return False

    print(
        f"[Batch] First batch of week {current_iso_week} — generating weekly review..."
    )
    if reviewer.generate_weekly_review(review_doc_id, base_date):
        collector.state_manager.update_weekly_review_week(current_iso_week)
        return True

    # 실패 시 주차를 기록하지 않아 다음 실행에서 재시도된다
    print("[Batch] 주간리뷰 생성 실패 — 다음 배치에서 재시도합니다.", file=sys.stderr)
    return False


def run_batch(
    collector: MessageCollector,
    triage: ResearchTriage,
    analyzer: ResearchAnalyzer,
    writer: ResearchWriter,
    merger: CommentMerger,
    reviewer: WeeklyReviewer,
    thought_doc_id: str,
    original_doc_resolver: Callable[[datetime], str],
    doc_mappings: Dict[str, str],
    channel_id: str,
    base_date: datetime
) -> bool:
    """일일 배치 전체 파이프라인 오케스트레이션.

    LLM 호출은 '판별(triage)' 과 '추출(extract)' 두 단계에서 **배치로만** 일어난다.
    호출 1건당 input 약 20.7k 토큰이 고정 오버헤드이므로 건별 호출은 낭비다.
    """

    # 1. 생각노트 역동기화 병합 (어제 작성한 의견 동기화)
    print("[Batch] Step 1: Merging yesterday's thoughts...")
    merger.merge_thoughts(thought_doc_id)

    # 2. 신규 텔레그램 메시지 수집 (필터링 없이 100% 수집)
    print("[Batch] Step 2: Collecting new messages...")
    new_messages = collector.collect_new_messages(channel_id)
    if not new_messages:
        print("[Batch] No new messages to process.")
        run_weekly_review_if_due(collector, reviewer, doc_mappings, base_date)
        return True

    # 3. 리서치 여부 판별 (프로그램 확정 + triage 워커 배치)
    print(f"[Batch] Step 3: Triaging {len(new_messages)} messages...")
    decisions = triage.classify(new_messages)
    research_ids = [m.id for m in new_messages if decisions[m.id].is_research]
    no_body_ids = {
        m.id for m in new_messages
        if decisions[m.id].reason == REASON_NO_BODY
    }
    fallback_ids = triage_fallback_ids(triage)
    print(
        f"[Batch] Triage: research={len(research_ids)}, "
        f"skipped={len(new_messages) - len(research_ids)} "
        f"(본문 없음 {len(no_body_ids)}건)"
        + (f", 판별 폴백 {len(fallback_ids)}건 {fallback_ids}" if fallback_ids else "")
    )

    # 4. 리서치 글을 배치로 분석. 실패하면 아무것도 기록하지 않고 중단한다.
    print(f"[Batch] Step 4: Extracting logic from {len(research_ids)} research posts...")
    reports = analyzer.analyze_batch(
        [(m.id, m.text) for m in new_messages if m.id in set(research_ids)]
    )

    success_count = 0
    skipped_count = 0
    last_success_id = None

    for msg in new_messages:
        try:
            # 본문이 없는 미디어 전용 메시지는 보관할 원문이 없으므로 건너뛴다.
            if msg.id in no_body_ids:
                skipped_count += 1
                last_success_id = msg.id
                collector.state_manager.update_channel_state(channel_id, msg.id)
                continue

            title = msg.text.split("\n")[0][:30].strip() or "제목 없음"
            report = reports.get(msg.id)

            # 5. 원문 보관 (월별 문서가 없으면 자동 생성, 확보 실패 시 예외로 중단)
            target_original_doc = original_doc_resolver(msg.date)
            writer.write_original_backup(target_original_doc, msg, title=title)

            target_doc_name = "원문보관"
            link_doc_id = target_original_doc

            if report is not None:
                # 테마는 enum 으로 강제되므로 매핑이 반드시 존재한다.
                # 조용한 폴백은 오분류를 쌓기 때문에 두지 않는다.
                target_doc_id = doc_mappings.get(report.theme)
                if not target_doc_id:
                    raise KeyError(
                        f"테마 {report.theme!r} 에 대응하는 문서 매핑이 없습니다. "
                        "doc_mappings 를 확인하십시오."
                    )
                writer.write_research_report(
                    target_doc_id, msg, report, prefix="r", title=title
                )
                target_doc_name = report.theme
                link_doc_id = target_doc_id
                success_count += 1
            else:
                skipped_count += 1

            # 6. 생각노트 Pre-fill 생성
            writer.pre_fill_thought_note(
                thought_doc_id,
                msg,
                prefix="r",
                title=title,
                target_doc_name=target_doc_name,
                target_doc_id=link_doc_id
            )

            # 메시지 하나를 다 쓴 즉시 state 에 **기록**한다.
            #
            # 메모리 변수만 갱신하고 루프 끝에서 한 번 저장하면, 예외가 아니라
            # 프로세스가 죽는 경우(VM 종료, 정전, 강제 종료)에 아무것도 저장되지
            # 않는다. 그러면 다음 실행이 같은 메시지를 다시 기록해 문서에 중복
            # 블록이 쌓인다 (과거 298블록 중복의 원인과 같은 구조).
            #
            # 실제로 2026-08-11 배치는 이 저장 직후 09:04 에 VM 이 꺼졌다.
            # 4분만 빨랐어도 37건이 중복 기록됐을 것이다.
            last_success_id = msg.id
            collector.state_manager.update_channel_state(channel_id, msg.id)

        except Exception as e:
            # 예외 경로에서도 한 번 더 저장한다. 위에서 이미 저장했으므로 보통은
            # 같은 값이지만, 예외가 저장 자체에서 났을 경우를 대비한다.
            print(
                f"[Batch] Error during pipeline execution at message {msg.id}: "
                f"{str(e)[:400]}",
                file=sys.stderr,
            )
            if last_success_id:
                collector.state_manager.update_channel_state(channel_id, last_success_id)
            raise e

    # 7. 그 주(ISO Week) 첫 배치라면 지난주 주간리뷰 생성
    run_weekly_review_if_due(collector, reviewer, doc_mappings, base_date)

    # 8. 전체 성공 시 상태 최종 업데이트
    if last_success_id:
        collector.state_manager.update_channel_state(channel_id, last_success_id)

    print(f"[Batch] Pipeline completed successfully. Processed={success_count}, Skipped={skipped_count}")
    return True

def run_backfill(
    collector: MessageCollector,
    triage: ResearchTriage,
    analyzer: ResearchAnalyzer,
    writer: ResearchWriter,
    thought_doc_id: str,
    original_doc_resolver: Callable[[datetime], str],
    doc_mappings: Dict[str, str],
    channel_id: str,
    from_id: int,
    to_id: int,
) -> bool:
    """누락된 메시지 구간을 백필하는 파이프라인. state.json을 갱신하지 않는다."""

    print(f"[Backfill] Collecting messages in range [{from_id}, {to_id}]...")
    messages = collector.collect_message_range(channel_id, from_id, to_id)
    if not messages:
        print("[Backfill] No messages found in the specified range.")
        return True

    decisions = triage.classify(messages)
    research_ids = {m.id for m in messages if decisions[m.id].is_research}
    no_body_ids = {
        m.id for m in messages if decisions[m.id].reason == REASON_NO_BODY
    }
    reports = analyzer.analyze_batch(
        [(m.id, m.text) for m in messages if m.id in research_ids]
    )

    success_count = 0
    skipped_count = 0

    for msg in messages:
        try:
            if msg.id in no_body_ids:
                skipped_count += 1
                continue

            title = msg.text.split("\n")[0][:30].strip() or "제목 없음"
            report = reports.get(msg.id)

            # 월별 원문보관 문서 라우팅 (없으면 자동 생성)
            target_original_doc = original_doc_resolver(msg.date)
            writer.write_original_backup(target_original_doc, msg, title=title)

            target_doc_name = "원문보관"
            link_doc_id = target_original_doc

            if report is not None:
                target_doc_id = doc_mappings.get(report.theme)
                if not target_doc_id:
                    raise KeyError(f"테마 {report.theme!r} 문서 매핑 없음")
                writer.write_research_report(
                    target_doc_id, msg, report, prefix="r", title=title
                )
                target_doc_name = report.theme
                link_doc_id = target_doc_id
                success_count += 1
            else:
                skipped_count += 1

            # 생각노트 Pre-fill
            writer.pre_fill_thought_note(
                thought_doc_id, 
                msg, 
                prefix="r", 
                title=title,
                target_doc_name=target_doc_name,
                target_doc_id=link_doc_id
            )

        except Exception as e:
            print(f"[Backfill] Error processing message {msg.id}: {e}", file=sys.stderr)
            continue  # 백필 모드에서는 개별 실패를 건너뛰고 계속 진행

    print(f"[Backfill] Completed. Processed={success_count}, Skipped={skipped_count}")
    return True

def verify_theme_mappings(doc_mappings: Dict[str, str]) -> None:
    """모든 테마에 문서 매핑이 있는지 시작 시 검증한다 (프로그램 책임).

    테마가 enum 으로 강제되므로 라우팅 실패는 설정 오류다. 배치 중간에 발견하는 대신
    기동 시점에 즉시 실패시켜, 조용한 폴백으로 오분류가 쌓이는 것을 막는다.
    """
    missing = [theme for theme in THEMES if not doc_mappings.get(theme)]
    if missing:
        print(
            f"[Config] 문서 매핑이 없는 테마: {missing}. "
            "analyzer.THEMES 와 doc_mappings 를 일치시키십시오.",
            file=sys.stderr,
        )
        sys.exit(1)


def main():
    args = parse_args()

    # 구글 Docs 매핑 정보 및 파일 상태 경로 설정
    state_file = os.getenv("STATE_FILE_PATH", "./var/state.json")

    # 배치/리뷰 모드: 중복 실행 방지 잠금 획득
    if args.mode in ("batch", "review", "merge", "backfill", "repair", "restore"):
        if not require_lock(args.mode.upper(), state_file):
            sys.exit(0)

    thought_doc_id = os.getenv("THOUGHT_DOC_ID", "1p0d-bEHdVTzQjRnmXzzzB5HRvUCQSUyktUpksV70TUc")
    original_doc_id = os.getenv("ORIGINAL_DOC_ID", "17b8iLt_KxjWZX7On9hnPXCLZtm6fpT8GBgYb_X85PHY")
    
    # AGENTS.md 문서 매핑 테이블 — 모든 테마/종목 문서를 등록
    doc_mappings = {
        "원문보관": original_doc_id,
        "반도체사이클": os.getenv("THEME_SEMI_DOC_ID", "1UeI5DBboBk-Tedvu4G6ucOuZdxJAtkRg55N_VMV4U-s"),
        "시장심리·수급": os.getenv("THEME_SENTIMENT_DOC_ID", "1k2vvUvzgl6iW08MGbY9oehc2zBnKtPAi-z6OqrwaSoA"),
        "환율·매크로": os.getenv("THEME_MACRO_DOC_ID", "18lU0RmfkofcRuosdPn664x4AG8DSO12gvsDqLUi2eKE"),
        "금리": os.getenv("THEME_RATE_DOC_ID", "1PK88LaNGhBrxGC0xFGX4tkTPUU15kqzuOhK5SzQ74Eo"),
        "방산·방위산업": os.getenv("THEME_DEFENSE_DOC_ID", "1AvDpijnMrsi92m5OGg2APcg8xfniGA-jiXkiA6ygIJQ"),
        "ESS·2차전지": os.getenv("THEME_ESS_DOC_ID", "1lqWAS2R_U44GCIbmLoBfhcGAkjRPiAXthZpIC4W3UHA"),
        "한화에어로스페이스": os.getenv("STOCK_HANWHA_DOC_ID", "1F2YJCAtqznASlhY9351UA0qX-81bw2hbrqQM6qrrd9I"),
        "에이유브랜즈": os.getenv("STOCK_AUBRANDS_DOC_ID", "1cUfOHw0OAV0Wwpi5qM4aR3BSi2-H76aV7F8L9RtK-ek"),
        "에이피알": os.getenv("STOCK_APR_DOC_ID", "1zC3oDw1kbP651QjsPHztgzo7aCrZr7qPczddgdwmEvY"),
        "thought": thought_doc_id,
        "review": os.getenv("REVIEW_DOC_ID", "1ZMxc48JJgrlCmkKmE9SgRH3doRu4yasG3oymyvWDFHs"),
    }

    # 월별 원문보관 문서 매핑 (YYYY-MM → Google Docs ID).
    # ORIGINAL_DOC_MONTHLY 로 주입하고, 기존 운영 문서는 하위 호환을 위해 기본값으로 유지한다.
    # 미등록 월은 OriginalDocRegistry 가 문서를 새로 만들어 state.json 에 등록한다.
    original_doc_mappings = {
        "2026-07": original_doc_id,
        "2026-08": os.getenv("ORIGINAL_DOC_08_ID", "1gfyz-nJZ5vtcH8enwnxcGgjjpZ6Smoqj0RvVDix5BMI"),
    }
    original_doc_mappings.update(parse_monthly_mapping(os.getenv("ORIGINAL_DOC_MONTHLY")))

    # 되쓰기(restore) 모드가 원본 문서 ID 를 알아야 하므로 교체 전 값을 보관한다
    original_doc_defaults = dict(doc_mappings)

    # 문서 복구로 재생성된 문서가 있으면 매핑을 교체한다 (state.json 에 기록됨)
    _bootstrap_state = StateManager(state_file).load_state()
    overrides = getattr(_bootstrap_state, "doc_overrides", None) or {}
    if overrides:
        doc_mappings.update(overrides)
        print(f"[Config] 재생성 문서 매핑 적용: {list(overrides)}")

    verify_theme_mappings(doc_mappings)

    # 2. 서비스 인스턴스화 — LLM 은 agy 워커로 실행한다 (OpenAI 키 불필요)
    state_manager = StateManager(state_file)
    telegram_client = MCPTelegramClient()
    collector = MessageCollector(telegram_client, state_manager)

    roster = load_roster()
    runner = AgyCliRunner()
    triage = ResearchTriage(runner, roster["triage"])
    analyzer = ResearchAnalyzer(runner, roster["extract"])

    docs_client = MCPGoogleDocsClient()
    writer = ResearchWriter(docs_client)
    merger = CommentMerger(docs_client, doc_mappings)

    original_doc_registry = OriginalDocRegistry(
        docs_client=docs_client,
        state_manager=state_manager,
        configured=original_doc_mappings,
        drive_folder_id=os.getenv("DRIVE_PARENT_FOLDER_ID", ""),
    )

    # --model 로 리뷰 워커 모델을 덮어쓸 수 있다 (지정하지 않으면 로스터 기본값)
    review_spec = roster["review"]
    if args.model:
        review_spec = review_spec.with_model(args.model)

    reviewer = WeeklyReviewer(
        docs_client=docs_client,
        runner=runner,
        spec=review_spec,
        doc_mappings=doc_mappings,
    )

    # 배치 기준 시각은 현지(KST). 주간리뷰 7일 창과 ISO 주차 경계가 이 값을 따른다.
    now = local_now()

    print(
        "[Config] 워커 로스터: "
        + ", ".join(
            f"{role}={s.model}(batch {s.batch_size})" for role, s in roster.items()
        )
    )

    notifier = BatchNotifier(
        telegram_client=telegram_client,
        docs_client=docs_client,
        thought_doc_id=thought_doc_id,
        enabled=os.getenv("NOTIFY_ENABLED", "1") not in ("0", "false", "False"),
    )

    # 3. Mode 스위칭 분기 처리
    if args.mode == "batch":
        # 배치 실패를 사용자가 스케줄러 이력을 뒤지지 않고도 알 수 있어야 한다.
        # 실패하면 알린 뒤 예외를 그대로 올려 exit code 1 을 유지한다.
        try:
            # 모델이 사라졌으면 판별 폴백·리뷰 침묵 실패 전에 여기서 원인과 함께 멈춘다
            verify_roster_models({**roster, "review": review_spec}, runner)
            run_batch(
                collector=collector,
                triage=triage,
                analyzer=analyzer,
                writer=writer,
                merger=merger,
                reviewer=reviewer,
                thought_doc_id=thought_doc_id,
                original_doc_resolver=original_doc_registry.resolve,
                doc_mappings=doc_mappings,
                channel_id=args.channel,
                base_date=now
            )
        except BaseException as e:
            state = state_manager.load_state()
            state_manager.record_batch_result(ok=False, error=f"{type(e).__name__}: {e}")
            notifier.notify_failure(
                summary=f"일일 배치가 {type(e).__name__} 으로 중단되었습니다.",
                detail=str(e),
                context=AlertContext(
                    mode="batch",
                    when=now,
                    last_message_id=state.last_message_id,
                    hint="기록되지 않은 글은 state.json 이 갱신되지 않아 다음 실행에서 재시도됩니다.",
                ),
            )
            raise

        previous = state_manager.record_batch_result(ok=True)
        report_batch_success(
            notifier=notifier,
            state_manager=state_manager,
            previous_result=previous,
            fallback_ids=triage_fallback_ids(triage),
            now=now,
        )
    elif args.mode == "merge":
        print("Running manual Merge mode...")
        merger.merge_thoughts(thought_doc_id)
        print("Manual Merge completed.")
    elif args.mode == "review":
        print("Running manual Weekly Review mode...")
        review_doc_id = doc_mappings.get("review")
        reviewer.generate_weekly_review(review_doc_id, now)
        print("Manual Weekly Review completed.")
    elif args.mode == "backfill":
        if args.from_id is None or args.to_id is None:
            print("[BACKFILL] --from-id 및 --to-id 인자가 필요합니다.", file=sys.stderr)
            sys.exit(1)
        print(f"Running Backfill mode for message range [{args.from_id}, {args.to_id}]...")
        run_backfill(
            collector=collector,
            triage=triage,
            analyzer=analyzer,
            writer=writer,
            thought_doc_id=thought_doc_id,
            original_doc_resolver=original_doc_registry.resolve,
            doc_mappings=doc_mappings,
            channel_id=args.channel,
            from_id=args.from_id,
            to_id=args.to_id,
        )
        print("Backfill completed.")
    elif args.mode == "repair":
        repairer = DocRepairer(
            docs_client=docs_client,
            analyzer=ResearchAnalyzer(runner, roster["repair"]),
            drive_folder_id=os.getenv("DRIVE_PARENT_FOLDER_ID", ""),
        )
        original_ids = list(dict.fromkeys(original_doc_mappings.values()))
        targets = (
            list(THEMES) if args.doc in (None, "all") else [args.doc]
        )
        for name in targets:
            target_id = doc_mappings.get(name)
            if not target_id:
                print(f"[Repair] 알 수 없는 문서 키: {name}", file=sys.stderr)
                continue
            try:
                res = repairer.repair(
                    doc_name=name,
                    doc_id=target_id,
                    original_doc_ids=original_ids,
                    dry_run=args.dry_run,
                )
            except Exception as e:
                print(f"[Repair] {name} 복구 실패: {str(e)[:300]}", file=sys.stderr)
                continue
            if res.new_doc_id:
                state_manager.register_doc_override(name, res.new_doc_id)
                print(
                    f"[Repair] {name}: 매핑을 새 문서로 교체했습니다 "
                    f"({res.new_doc_id}). AGENTS.md 매핑표도 갱신하십시오."
                )
        print("Repair completed.")
    elif args.mode == "restore":
        # 재생성본을 원본 문서로 되쓰고 문서를 하나로 정리한다.
        # doc_overrides 를 읽으므로, 위에서 이미 doc_mappings 에 반영된 값 대신
        # 원본 ID 를 별도로 확보해야 한다.
        restorer = DocRestorer(docs_client=docs_client, state_manager=state_manager)
        pending = dict(overrides)
        targets = list(pending) if args.doc in (None, "all") else [args.doc]
        for name in targets:
            regenerated = pending.get(name)
            original = original_doc_defaults.get(name)
            if not regenerated:
                print(f"[Restore] {name}: 재생성본이 없습니다 (doc_overrides 미등록).",
                      file=sys.stderr)
                continue
            if not original:
                print(f"[Restore] {name}: 원본 문서 ID 를 알 수 없습니다.", file=sys.stderr)
                continue
            if args.dry_run:
                print(f"[Restore] (dry-run) {name}: {regenerated} → {original}")
                continue
            restorer.restore(name, original, regenerated)
        print("Restore completed.")
    elif args.mode == "status":
        state = state_manager.load_state()
        # state 에 저장된 시각은 UTC 다. now(=datetime.now(), naive 로컬)를 그대로 넘기면
        # KST 기준 9시간만큼 경과 시간이 부풀려져 "방금 성공"이 "9시간 전"으로 보인다.
        verdict, reasons = evaluate_batch_health(state, datetime.now(timezone.utc))

        print("=== autoNote 배치 상태 ===")
        print(f"판정: {verdict}")
        for reason in reasons:
            print(f"  - {reason}")
        print()
        print(f"마지막 처리 message ID : {state.last_message_id}")
        print(f"마지막 수집 시각       : {state.last_run}")
        print(f"마지막 배치 결과       : {state.last_batch_result or '기록 없음'}")
        print(f"마지막 배치 시각       : {state.last_batch_at or '기록 없음'}")
        if state.last_batch_error:
            print(f"마지막 오류            : {state.last_batch_error[:300]}")
        print(f"주간리뷰 마지막 주차   : {state.last_weekly_review_week or '기록 없음'}")
        ch_state = state.channels.get(args.channel)
        if ch_state:
            print(f"채널 {args.channel} 마지막 ID : {ch_state.last_message_id}")

        if verdict != VERDICT_OK:
            sys.exit(2)

if __name__ == "__main__":
    main()
