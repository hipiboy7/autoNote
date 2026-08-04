import os
import sys
import argparse
import atexit
import msvcrt
from datetime import datetime
from typing import Dict

# 라이브러리 및 서비스 모듈 import
from telegram_client.mcp_client import MCPTelegramClient
from llm_gateway.openai_gateway import OpenAILikeGateway
from google_docs_client.mcp_client import MCPGoogleDocsClient

from collector.service import MessageCollector, StateManager
from analyzer.service import ResearchAnalyzer
from analyzer.config import LLMSettings
from writer.service import ResearchWriter
from merger.service import CommentMerger
from reviewer.service import WeeklyReviewer

def parse_args():
    parser = argparse.ArgumentParser(description="autoNote 투자 리서치 하이브리드 배치 CLI")
    parser.add_argument(
        "--model", 
        type=str, 
        default=os.getenv("CHAT_LLM_MODEL", "Gemini 3.5 Flash (Medium)"),
        help="사용할 LLM 모델명 지정"
    )
    parser.add_argument(
        "--channel", 
        type=str, 
        default="-1004421517798",
        help="수집할 텔레그램 채널 ID"
    )
    parser.add_argument(
        "--mode", 
        type=str, 
        choices=["batch", "merge", "review", "status", "backfill"], 
        default="batch",
        help="배치 실행 모드 지정"
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

# --- 배치 중복 실행 방지 파일 잠금 (Windows) ---
_lock_fd = None

def acquire_batch_lock(lock_path: str) -> bool:
    """배치 중복 실행 방지를 위한 파일 잠금 획득.
    
    msvcrt.locking()을 사용하여 배타적 잠금을 시도합니다.
    이미 다른 프로세스가 잠금을 보유 중이면 False를 반환합니다.
    """
    global _lock_fd
    try:
        _lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT)
        # 비차단 배타적 잠금 시도 (1바이트)
        msvcrt.locking(_lock_fd, msvcrt.LK_NBLCK, 1)
        # 잠금 성공 후 PID 기록 (디버깅 용도)
        os.lseek(_lock_fd, 0, os.SEEK_SET)
        os.write(_lock_fd, str(os.getpid()).encode())
        atexit.register(release_batch_lock, lock_path)
        return True
    except (IOError, OSError):
        if _lock_fd is not None:
            os.close(_lock_fd)
            _lock_fd = None
        return False

def release_batch_lock(lock_path: str):
    """배치 잠금 해제 및 잠금 파일 정리."""
    global _lock_fd
    if _lock_fd is not None:
        try:
            os.lseek(_lock_fd, 0, os.SEEK_SET)
            msvcrt.locking(_lock_fd, msvcrt.LK_UNLCK, 1)
            os.close(_lock_fd)
        except Exception:
            pass
        _lock_fd = None
    try:
        os.remove(lock_path)
    except Exception:
        pass

def resolve_original_doc(msg_date: datetime, original_doc_mappings: Dict[str, str], fallback_doc_id: str) -> str:
    """메시지 날짜 기반으로 올바른 월별 원문보관 문서 ID를 반환한다."""
    month_key = msg_date.strftime("%Y-%m")
    return original_doc_mappings.get(month_key, fallback_doc_id)

def run_batch(
    collector: MessageCollector,
    analyzer: ResearchAnalyzer,
    writer: ResearchWriter,
    merger: CommentMerger,
    reviewer: WeeklyReviewer,
    thought_doc_id: str,
    original_doc_id: str,
    original_doc_mappings: Dict[str, str],
    doc_mappings: Dict[str, str],
    channel_id: str,
    base_date: datetime
) -> bool:
    """일일 배치 전체 파이프라인 오케스트레이션"""
    
    # 1. 생각노트 역동기화 병합 (어제 작성한 의견 동기화)
    print("[Batch] Step 1: Merging yesterday's thoughts...")
    merger.merge_thoughts(thought_doc_id)

    # 2. 신규 텔레그램 메시지 수집 (필터링 없이 100% 수집)
    print("[Batch] Step 2: Collecting new messages...")
    new_messages = collector.collect_new_messages(channel_id)
    if not new_messages:
        print("[Batch] No new messages to process.")
        return True

    success_count = 0
    skipped_count = 0
    last_success_id = None

    for msg in new_messages:
        try:
            title = msg.text.split("\n")[0][:30].strip() or "제목 없음"

            # 3. 리서치 분석을 Docs 기록보다 먼저 수행한다.
            #    LLM 실패 시 아무것도 기록하지 않은 상태로 중단해야 한다. 원문을 먼저 쓰면
            #    state.json 이 갱신되지 않은 채 배치가 중단되어 다음 실행에서 원문이 중복 기록된다.
            is_res = collector.is_research(msg)
            report = analyzer.analyze_message(msg.text) if is_res else None

            # 4. 원문 무조건 보관 (제목은 첫 30자 요약)
            target_original_doc = resolve_original_doc(msg.date, original_doc_mappings, original_doc_id)
            writer.write_original_backup(target_original_doc, msg, title=title)

            target_doc_id = None
            target_doc_name = "원문보관"
            link_doc_id = target_original_doc

            if report is not None:
                # LLM이 분류한 테마명으로 대상 문서 조회 (매칭 실패 시 반도체사이클 폴백)
                target_doc_id = doc_mappings.get(report.theme) or doc_mappings.get("반도체사이클")
                if target_doc_id:
                    writer.write_research_report(target_doc_id, msg, report, prefix="r", title=title)
                    target_doc_name = report.theme
                    link_doc_id = target_doc_id
                success_count += 1
            else:
                skipped_count += 1

            # 5. 생각노트 Pre-fill 생성 (모든 원문에 대해서 적용)
            writer.pre_fill_thought_note(
                thought_doc_id, 
                msg, 
                prefix="r", 
                title=title,
                target_doc_name=target_doc_name,
                target_doc_id=link_doc_id
            )

            # 각 메시지 성공 완료 시점마다 last_success_id 업데이트 (원자적 체크포인트)
            last_success_id = msg.id

        except Exception as e:
            # 배치 중단 시 직전 성공 ID까지 안전 세이브하여 중복/유실 차단
            print(f"[Batch] Error during pipeline execution at message {msg.id}: {e}", file=sys.stderr)
            if last_success_id:
                collector.state_manager.update_channel_state(channel_id, last_success_id)
            raise e

    # 6. 주말이 지나고 그 주(ISO Week)에 처음으로 배치가 실행될 때 지난주 주간리뷰 자동 실행
    current_iso_week = base_date.strftime("%G-W%V")
    state = collector.state_manager.load_state()
    if state.last_weekly_review_week != current_iso_week:
        print(f"[Batch] Step 6: First batch execution of week {current_iso_week}. Triggering weekly review generation...")
        review_doc_id = doc_mappings.get("review")
        if review_doc_id:
            reviewer.generate_weekly_review(review_doc_id, base_date)
            collector.state_manager.update_weekly_review_week(current_iso_week)

    # 7. 전체 성공 시 상태 최종 업데이트
    if last_success_id:
        collector.state_manager.update_channel_state(channel_id, last_success_id)

    print(f"[Batch] Pipeline completed successfully. Processed={success_count}, Skipped={skipped_count}")
    return True

def run_backfill(
    collector: MessageCollector,
    analyzer: ResearchAnalyzer,
    writer: ResearchWriter,
    thought_doc_id: str,
    original_doc_id: str,
    original_doc_mappings: Dict[str, str],
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

    success_count = 0
    skipped_count = 0

    for msg in messages:
        try:
            title = msg.text.split("\n")[0][:30].strip() or "제목 없음"

            # 분석을 Docs 기록보다 먼저 수행한다 (LLM 실패 시 원문 중복 기록 방지).
            is_res = collector.is_research(msg)
            report = analyzer.analyze_message(msg.text) if is_res else None

            # 월별 원문보관 문서 라우팅
            target_original_doc = resolve_original_doc(msg.date, original_doc_mappings, original_doc_id)
            writer.write_original_backup(target_original_doc, msg, title=title)

            target_doc_name = "원문보관"
            link_doc_id = target_original_doc

            if report is not None:
                target_doc_id = doc_mappings.get(report.theme) or doc_mappings.get("반도체사이클")
                if target_doc_id:
                    writer.write_research_report(target_doc_id, msg, report, prefix="r", title=title)
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

def main():
    args = parse_args()
    
    # 1. 설정 로드
    try:
        settings = LLMSettings()
    except Exception as e:
        print(f"Error loading LLM Settings. Please check env variables: {e}", file=sys.stderr)
        sys.exit(1)

    # 구글 Docs 매핑 정보 및 파일 상태 경로 설정
    state_file = os.getenv("STATE_FILE_PATH", "./data/state.json")

    # 배치/리뷰 모드: 중복 실행 방지 잠금 획득
    if args.mode in ("batch", "review", "merge", "backfill"):
        lock_path = os.path.join(os.path.dirname(state_file), "batch.lock")
        if not acquire_batch_lock(lock_path):
            print(f"[{args.mode.upper()}] 다른 배치 프로세스가 이미 실행 중입니다. 종료합니다.", file=sys.stderr)
            sys.exit(0)
        print(f"[{args.mode.upper()}] 배치 잠금 획득 완료 (PID: {os.getpid()})")

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
        "한화에어로스페이스": os.getenv("STOCK_HANWHA_DOC_ID", "1F2YJCAtqznASlhY9351UA0qX-81bw2hbrqQM6qrrd9I"),
        "에이유브랜즈": os.getenv("STOCK_AUBRANDS_DOC_ID", "1cUfOHw0OAV0Wwpi5qM4aR3BSi2-H76aV7F8L9RtK-ek"),
        "에이피알": os.getenv("STOCK_APR_DOC_ID", "1zC3oDw1kbP651QjsPHztgzo7aCrZr7qPczddgdwmEvY"),
        "thought": thought_doc_id,
        "review": os.getenv("REVIEW_DOC_ID", "1ZMxc48JJgrlCmkKmE9SgRH3doRu4yasG3oymyvWDFHs"),
    }

    # 월별 원문보관 문서 매핑 (YYYY-MM → Google Docs ID)
    original_doc_mappings = {
        "2026-07": original_doc_id,
        "2026-08": os.getenv("ORIGINAL_DOC_08_ID", "1gfyz-nJZ5vtcH8enwnxcGgjjpZ6Smoqj0RvVDix5BMI"),
    }

    # 2. 서비스 인스턴스화
    state_manager = StateManager(state_file)
    telegram_client = MCPTelegramClient()
    collector = MessageCollector(telegram_client, state_manager)
    
    llm_gateway = OpenAILikeGateway()
    analyzer = ResearchAnalyzer(llm_gateway, settings)
    
    docs_client = MCPGoogleDocsClient()
    writer = ResearchWriter(docs_client)
    merger = CommentMerger(docs_client, doc_mappings)
    
    reviewer = WeeklyReviewer(
        docs_client=docs_client,
        llm_gateway=llm_gateway,
        doc_mappings=doc_mappings,
        model_name=args.model,
        base_url=settings.reasoning_llm_base_url,
        api_key=settings.reasoning_llm_api_key
    )

    now = datetime.now()

    # 3. Mode 스위칭 분기 처리
    if args.mode == "batch":
        run_batch(
            collector=collector,
            analyzer=analyzer,
            writer=writer,
            merger=merger,
            reviewer=reviewer,
            thought_doc_id=thought_doc_id,
            original_doc_id=original_doc_id,
            original_doc_mappings=original_doc_mappings,
            doc_mappings=doc_mappings,
            channel_id=args.channel,
            base_date=now
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
            analyzer=analyzer,
            writer=writer,
            thought_doc_id=thought_doc_id,
            original_doc_id=original_doc_id,
            original_doc_mappings=original_doc_mappings,
            doc_mappings=doc_mappings,
            channel_id=args.channel,
            from_id=args.from_id,
            to_id=args.to_id,
        )
        print("Backfill completed.")
    elif args.mode == "status":
        state = state_manager.load_state()
        print("=== autoNote System Status ===")
        print(f"Last global message ID: {state.last_message_id}")
        print(f"Last run timestamp: {state.last_run}")
        ch_state = state.channels.get(args.channel)
        if ch_state:
            print(f"Channel {args.channel} last message ID: {ch_state.last_message_id}")
            print(f"Channel {args.channel} last run timestamp: {ch_state.last_run}")

if __name__ == "__main__":
    main()
