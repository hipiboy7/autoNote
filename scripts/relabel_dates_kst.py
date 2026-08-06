"""과거 기록의 날짜 라벨을 UTC 기준에서 KST 기준으로 바로잡는다 (일회성).

경위: writer 가 메시지 시각(UTC)을 그대로 `%Y-%m-%d` 로 찍었다. KST = UTC+9 이므로
한국 시간 오전 0~9시에 올라온 글이 **전날로 기록**됐다. writer 는 고쳤으므로 새 기록은
정확하지만, 이미 찍힌 라벨은 이 스크립트로 보정한다.

동작:
  1. 텔레그램에서 메시지 시각을 가져와 식별자 → 올바른 KST 날짜 맵을 만든다.
  2. 각 문서의 블록 헤더 `■ [r811] 2026-08-05 | 제목` 을 찾아 날짜가 다르면 교체한다.
  3. 문서 단위로 본문을 재작성한다. 건당 replace_line 은 매번 전체 문서 읽기 검증이
     따라붙어 수백 건이면 비현실적으로 느리다.
  4. 재작성 후 남은 불일치 수를 다시 세어 검증한다. 잔존이 있으면 실패로 보고한다.

사용법:
    python scripts/relabel_dates_kst.py --dry-run          # 대상만 집계
    python scripts/relabel_dates_kst.py                    # 전체 문서 보정
    python scripts/relabel_dates_kst.py 원문보관 금리         # 특정 문서만
    python scripts/relabel_dates_kst.py --limit 500        # 텔레그램 조회 범위 확대

종료 코드: 0 성공(또는 대상 없음) / 1 일부 실패
"""

import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _rel in (
    os.path.join("program", "src"),
    os.path.join("program", "libs", "agy-gateway", "src"),
    os.path.join("program", "libs", "google-docs-client", "src"),
    os.path.join("program", "libs", "telegram-client", "src"),
    os.path.join("program", "services", "analyzer", "src"),
    os.path.join("program", "services", "collector", "src"),
    os.path.join("program", "services", "writer", "src"),
):
    sys.path.insert(0, os.path.join(_ROOT, _rel))

os.chdir(_ROOT)

from batch_lock import require_lock
from google_docs_client.mcp_client import MCPGoogleDocsClient
from restore_docs import rewrite_document
from telegram_client.mcp_client import MCPTelegramClient
from writer import local_date_str

# 보정 대상 문서 (원문보관 + 테마/종목)
DOCS = {
    "원문보관 2026-07": "17b8iLt_KxjWZX7On9hnPXCLZtm6fpT8GBgYb_X85PHY",
    "원문보관 2026-08": "1gfyz-nJZ5vtcH8enwnxcGgjjpZ6Smoqj0RvVDix5BMI",
    "반도체사이클": "1UeI5DBboBk-Tedvu4G6ucOuZdxJAtkRg55N_VMV4U-s",
    "시장심리·수급": "1k2vvUvzgl6iW08MGbY9oehc2zBnKtPAi-z6OqrwaSoA",
    "환율·매크로": "18lU0RmfkofcRuosdPn664x4AG8DSO12gvsDqLUi2eKE",
    "금리": "1PK88LaNGhBrxGC0xFGX4tkTPUU15kqzuOhK5SzQ74Eo",
    "방산·방위산업": "1AvDpijnMrsi92m5OGg2APcg8xfniGA-jiXkiA6ygIJQ",
    "한화에어로스페이스": "1F2YJCAtqznASlhY9351UA0qX-81bw2hbrqQM6qrrd9I",
    "에이유브랜즈": "1cUfOHw0OAV0Wwpi5qM4aR3BSi2-H76aV7F8L9RtK-ek",
    "에이피알": "1zC3oDw1kbP651QjsPHztgzo7aCrZr7qPczddgdwmEvY",
}

# ■ [r811] 2026-08-05 | 제목
HEADER = re.compile(r"■\s*\[([a-zA-Z]?\d+)\]\s*(\d{4}-\d{2}-\d{2})\s*\|")


def build_correct_dates(limit: int) -> dict:
    """메시지 ID → 올바른 KST 날짜 문자열."""
    channel = os.getenv("TELEGRAM_CHANNEL_ID", "-1004421517798")
    messages = MCPTelegramClient().list_messages(channel, limit=limit)
    return {str(m.id): local_date_str(m.date) for m in messages}


def main() -> int:
    argv = sys.argv[1:]
    dry_run = "--dry-run" in argv
    # 기본값을 넉넉히 잡는다. 400 이면 오래된 글의 시각을 못 얻어 조용히 건너뛰면서도
    # '완료' 로 보고된다 (실측: limit 400 에서 미확보 261건, limit 1000 에서 4건).
    limit = 1000
    if "--limit" in argv:
        try:
            limit = int(argv[argv.index("--limit") + 1])
        except (IndexError, ValueError):
            print("[Relabel] --limit 값을 읽을 수 없습니다.", file=sys.stderr)
            return 1
    names = [a for a in argv if not a.startswith("--") and not a.isdigit()]
    targets = names or list(DOCS)

    # 일일 배치(08:50)와 겹치면 배치가 방금 기록한 내용이 재작성으로 덮여 유실된다.
    # 런북에 "배치 시간대를 피하라" 고 적어두는 것만으로는 부족하므로 프로그램이 막는다.
    if not dry_run and not require_lock("RELABEL"):
        return 1

    print(f"[Relabel] 텔레그램에서 메시지 시각 조회 (limit={limit})...")
    correct = build_correct_dates(limit)
    print(f"[Relabel] 확보한 메시지 시각: {len(correct)}건")
    if not correct:
        print("[Relabel] 메시지 시각을 얻지 못했습니다. 중단합니다.", file=sys.stderr)
        return 1

    docs = MCPGoogleDocsClient()
    total_fixed = total_targets = total_unknown = failures = 0
    try:
        for name in targets:
            doc_id = DOCS.get(name)
            if not doc_id:
                print(f"[Relabel] 알 수 없는 문서 키: {name}", file=sys.stderr)
                failures += 1
                continue

            text = docs.read_text(doc_id)
            if not text:
                print(f"[Relabel] {name}: 문서를 읽지 못했습니다.", file=sys.stderr)
                failures += 1
                continue

            mismatches, unknown = [], []
            for match in HEADER.finditer(text):
                ident, labelled = match.group(1), match.group(2)
                digits = re.sub(r"\D", "", ident)
                expected = correct.get(digits)
                if expected is None:
                    unknown.append(ident)
                elif expected != labelled:
                    mismatches.append((match.group(0), ident, labelled, expected))

            total_targets += len(mismatches)
            total_unknown += len(unknown)
            print(f"[Relabel] {name}: 라벨 불일치 {len(mismatches)}건"
                  f" / 시각 미확보 {len(unknown)}건")
            for _, ident, was, now in mismatches[:5]:
                print(f"[Relabel]   [{ident}] {was} → {now}")
            if len(mismatches) > 5:
                print(f"[Relabel]   … 외 {len(mismatches) - 5}건")

            if dry_run or not mismatches:
                continue

            # 건당 replace_line 은 매번 전체 문서 읽기 검증이 따라붙어 수백 건이면
            # 비현실적으로 느리다. 문서 단위로 한 번에 재작성한다.
            corrected = text
            for header, _ident, was, now in mismatches:
                corrected = corrected.replace(header, header.replace(was, now, 1), 1)

            if not rewrite_document(docs, doc_id, corrected):
                print(f"[Relabel] {name}: 재작성 실패 — 보정하지 못했습니다.",
                      file=sys.stderr)
                failures += 1
                continue

            # 검증: 남은 불일치 수를 다시 센다
            after = docs.read_text(doc_id)
            remaining = 0
            for match in HEADER.finditer(after):
                digits = re.sub(r"\D", "", match.group(1))
                expected = correct.get(digits)
                if expected and expected != match.group(2):
                    remaining += 1
            fixed = len(mismatches) - remaining
            total_fixed += fixed
            status = "OK" if remaining == 0 else f"잔존 {remaining}건"
            print(f"[Relabel] {name}: {fixed}/{len(mismatches)}건 보정 완료 ({status})")
            if remaining:
                failures += 1

        print()
        print(f"[Relabel] 합계 — 대상 {total_targets}건, 보정 {total_fixed}건, "
              f"시각 미확보 {total_unknown}건, 실패 {failures}건")
        if total_unknown:
            print("[Relabel] 시각 미확보 블록은 --limit 를 늘려 다시 실행하십시오 "
                  "(텔레그램 조회 범위 밖의 오래된 글).")
    finally:
        docs.close()

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
