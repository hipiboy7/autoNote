"""원문보관 문서의 중복 블록을 정리하고 잘못 붙은 ID를 바로잡는다 (일회성).

경위: LLM 이 dummy-key 로 계속 실패하던 시기에 배치가 '원문을 먼저 기록한 뒤 분석'
하는 순서였다. 분석 실패로 배치가 중단되면 state.json 이 갱신되지 않아 다음 실행에서
같은 원문을 또 기록했다. 순서 버그는 고쳤고(2026-08-05) 8월 기록에는 중복이 없다.
남은 것은 그 시기에 쌓인 흔적이다.

판정 기준은 **텔레그램 원문과의 일치도** 하나다. 사람 판단이 개입하지 않는다.
AGENTS.md 가 원문보관을 "텔레그램 원문 그대로 수정 없이 보관" 으로 규정하므로
**원문 충실도가 높은 판본을 남긴다** (읽기 좋게 다듬어진 판본이 아니라).

처리:
  1. 내용이 완전히 같은 중복 → 첫 번째만 남긴다.
  2. 표기만 다른 중복 → 원문 일치도가 가장 높은 판본만 남긴다.
  3. 라벨 ID 가 틀린 블록 → 실제 원문의 ID 로 라벨을 교체한다.
     단 그 ID 가 문서에 이미 있으면 교체하지 않고 제거 대상으로 둔다 (중복 방지).

사용법:
    python scripts/dedupe_archive.py --dry-run
    python scripts/dedupe_archive.py
"""

import difflib
import os
import re
import sys
from collections import Counter, defaultdict

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
from repair_docs import parse_blocks
from restore_docs import rewrite_document
from telegram_client.mcp_client import MCPTelegramClient
from writer import BLOCK_SEPARATOR

ARCHIVES = {
    "원문보관 2026-07": "17b8iLt_KxjWZX7On9hnPXCLZtm6fpT8GBgYb_X85PHY",
    "원문보관 2026-08": "1gfyz-nJZ5vtcH8enwnxcGgjjpZ6Smoqj0RvVDix5BMI",
}

HEADER_LINE = re.compile(r"^\s*■[^\n]*\n", re.MULTILINE)
SOURCE_LINE = re.compile(r"▶\s*출처:[^\n]*\n")
# 라벨 교체 판정 임계치: 이보다 낮으면 '다른 메시지의 원문' 이라고 단정하지 않는다
RELABEL_MIN_RATIO = 0.85


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def body_of(block_text: str) -> str:
    """헤더와 출처 줄을 뺀 본문 (비교용 정규화)."""
    stripped = HEADER_LINE.sub("", block_text.strip() + "\n", count=1)
    return norm(SOURCE_LINE.sub("", stripped, count=1))


def ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a[:400], b[:400]).ratio()


def best_match(body: str, sources: dict) -> tuple:
    best_id, best_r = None, 0.0
    for mid, src in sources.items():
        if not src:
            continue
        r = ratio(body, src)
        if r > best_r:
            best_id, best_r = mid, r
    return best_id, best_r


def plan_document(text: str, sources: dict, existing_ids: set) -> tuple:
    """(새 본문, 통계) 를 만든다. 문서를 바꾸지 않는다."""
    blocks = parse_blocks(text)
    groups = defaultdict(list)
    for b in blocks:
        if b.identifier:
            groups[b.identifier].append(b)

    keep = set()          # id(block) 기준 유지 대상
    relabel = {}          # id(block) -> 새 식별자
    stats = Counter()
    notes = []

    for ident, members in groups.items():
        if len(members) == 1:
            keep.add(id(members[0]))
            continue

        msg_id = int(re.sub(r"\D", "", ident) or 0)
        source = sources.get(msg_id, "")

        scored = []
        for b in members:
            body = body_of(b.text)
            own = ratio(body, source) if source else 0.0
            mid, mr = best_match(body, sources) if body else (None, 0.0)
            scored.append({"block": b, "body": body, "own": own,
                           "best_id": mid, "best_r": mr})

        # 1) 라벨이 틀린 블록: 실제 원문 ID 로 교체 (그 ID 가 문서에 없을 때만)
        remaining = []
        for s in scored:
            wrong_label = (
                s["best_id"] is not None
                and s["best_id"] != msg_id
                and s["best_r"] >= RELABEL_MIN_RATIO
                and s["best_r"] - s["own"] >= 0.3
            )
            if wrong_label:
                new_ident = f"r{s['best_id']}"
                if new_ident in existing_ids or new_ident in relabel.values():
                    stats["relabel_skipped_conflict"] += 1
                    notes.append(f"[{ident}] → {new_ident} 교체 보류 (이미 존재)")
                    continue          # 제거 대상
                keep.add(id(s["block"]))
                relabel[id(s["block"])] = new_ident
                existing_ids.add(new_ident)
                stats["relabelled"] += 1
                notes.append(f"[{ident}] 블록을 [{new_ident}] 로 교체 (일치도 {s['best_r']:.2f})")
            else:
                remaining.append(s)

        if not remaining:
            continue

        # 2) 남은 것 중 원문 충실도가 가장 높은 판본만 유지
        bodies = {s["body"] for s in remaining}
        remaining.sort(key=lambda s: (-s["own"], -len(s["body"])))
        keep.add(id(remaining[0]["block"]))
        dropped = len(remaining) - 1
        if dropped:
            stats["identical_dropped" if len(bodies) == 1 else "variant_dropped"] += dropped

    # 새 본문 조립 (원래 순서 유지)
    parts = []
    for b in blocks:
        if not b.identifier:
            parts.append(b.text.strip())          # 문서 머리말
            continue
        if id(b) not in keep:
            continue
        chunk = b.text.strip()
        new_ident = relabel.get(id(b))
        if new_ident:
            chunk = chunk.replace(f"[{b.identifier}]", f"[{new_ident}]", 1)
        parts.append(f"{BLOCK_SEPARATOR}\n{chunk}")

    kept_blocks = len([b for b in blocks if b.identifier and id(b) in keep])
    stats["blocks_before"] = len([b for b in blocks if b.identifier])
    stats["blocks_after"] = kept_blocks
    return "\n".join(parts) + "\n", stats, notes


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    if not dry_run and not require_lock("DEDUPE"):
        return 1

    print("텔레그램 원문 조회 중...")
    sources = {
        m.id: norm(m.text)
        for m in MCPTelegramClient().list_messages(
            os.getenv("TELEGRAM_CHANNEL_ID", "-1004421517798"), limit=1000
        )
    }
    print(f"확보한 원문: {len(sources)}건\n")

    docs = MCPGoogleDocsClient()
    failures = 0
    try:
        texts = {name: docs.read_text(doc_id) for name, doc_id in ARCHIVES.items()}
        existing_ids = set()
        for text in texts.values():
            existing_ids.update(
                b.identifier for b in parse_blocks(text) if b.identifier
            )

        for name, doc_id in ARCHIVES.items():
            text = texts[name]
            if not text:
                print(f"[Dedupe] {name}: 문서를 읽지 못했습니다.", file=sys.stderr)
                failures += 1
                continue

            new_text, stats, notes = plan_document(text, sources, existing_ids)
            removed = stats["blocks_before"] - stats["blocks_after"]
            print(f"[Dedupe] {name}: 블록 {stats['blocks_before']} → "
                  f"{stats['blocks_after']} (제거 {removed})")
            print(f"[Dedupe]   동일 중복 제거 {stats['identical_dropped']}, "
                  f"표기차 제거 {stats['variant_dropped']}, "
                  f"라벨 교체 {stats['relabelled']}, "
                  f"교체 보류 {stats['relabel_skipped_conflict']}")
            for note in notes:
                print(f"[Dedupe]   {note}")

            if dry_run or removed == 0 and stats["relabelled"] == 0:
                continue

            if not rewrite_document(docs, doc_id, new_text):
                print(f"[Dedupe] {name}: 재작성 실패.", file=sys.stderr)
                failures += 1
                continue

            after = docs.read_text(doc_id)
            ids = [b.identifier for b in parse_blocks(after) if b.identifier]
            dup = [k for k, v in Counter(ids).items() if v > 1]
            status = "OK" if not dup else f"중복 잔존 {len(dup)}건"
            print(f"[Dedupe] {name}: 완료 (블록 {len(ids)}, {status})")
            if dup:
                failures += 1
    finally:
        docs.close()

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
