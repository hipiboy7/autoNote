"""재생성 문서를 구버전·원문보관과 대조 검증한다 (읽기 전용).

사용법:
    python verify_repair.py                 # state.json 의 doc_overrides 전부 검증
    python verify_repair.py 금리 방산·방위산업   # 특정 테마만
"""
import re
import sys

import os

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

from analyzer.grounding import (
    FABRICATION_MARKERS,
    find_fabrication_markers,
    ungrounded_numbers,
)
from collector.service import StateManager
from google_docs_client.mcp_client import MCPGoogleDocsClient
from repair_docs import (
    extract_evidence,
    parse_blocks,
    parse_original_sources,
    ungrounded_evidence,
)

# 구버전(오염) 문서 ID — main.py 의 기본 매핑과 동일
OLD_DOCS = {
    "반도체사이클": "1UeI5DBboBk-Tedvu4G6ucOuZdxJAtkRg55N_VMV4U-s",
    "시장심리·수급": "1k2vvUvzgl6iW08MGbY9oehc2zBnKtPAi-z6OqrwaSoA",
    "환율·매크로": "18lU0RmfkofcRuosdPn664x4AG8DSO12gvsDqLUi2eKE",
    "금리": "1PK88LaNGhBrxGC0xFGX4tkTPUU15kqzuOhK5SzQ74Eo",
    "방산·방위산업": "1AvDpijnMrsi92m5OGg2APcg8xfniGA-jiXkiA6ygIJQ",
    "한화에어로스페이스": "1F2YJCAtqznASlhY9351UA0qX-81bw2hbrqQM6qrrd9I",
    "에이유브랜즈": "1cUfOHw0OAV0Wwpi5qM4aR3BSi2-H76aV7F8L9RtK-ek",
    "에이피알": "1zC3oDw1kbP651QjsPHztgzo7aCrZr7qPczddgdwmEvY",
}
ORIGINALS = [
    "17b8iLt_KxjWZX7On9hnPXCLZtm6fpT8GBgYb_X85PHY",  # 2026-07
    "1gfyz-nJZ5vtcH8enwnxcGgjjpZ6Smoqj0RvVDix5BMI",  # 2026-08
]

overrides = getattr(StateManager("./data/state.json").load_state(), "doc_overrides", {}) or {}
targets = sys.argv[1:] or sorted(overrides)
if not targets:
    print("검증할 재생성 문서가 없습니다 (doc_overrides 비어 있음).")
    sys.exit(0)

docs = MCPGoogleDocsClient()
try:
    sources = parse_original_sources([docs.read_text(d) for d in ORIGINALS])
    print(f"원문보관에서 확보한 원문: {len(sources)}건\n")

    summary = []
    for name in targets:
        new_id = overrides.get(name)
        old_id = OLD_DOCS.get(name)
        if not new_id:
            print(f"[{name}] 재생성 문서가 없습니다 (doc_overrides 미등록). 건너뜁니다.\n")
            continue

        new_text = docs.read_text(new_id)
        old_text = docs.read_text(old_id) if old_id else ""
        new_blocks = [b for b in parse_blocks(new_text) if b.identifier]
        old_blocks = [b for b in parse_blocks(old_text) if b.identifier]

        new_ids = [b.identifier for b in new_blocks]
        old_ids = [b.identifier for b in old_blocks]
        preserved = set(old_ids) <= set(new_ids)
        order_ok = new_ids == old_ids
        new_markers = sum(new_text.count(m) for m in FABRICATION_MARKERS)
        old_markers = sum(old_text.count(m) for m in FABRICATION_MARKERS)

        # 재생성된 블록과 보존된 블록을 구분한다.
        # 복구는 '창작 표지가 있던 블록' 만 재생성하고 나머지는 원문 그대로 옮긴다.
        # 보존 블록의 근거 문제는 과거 수동/폴백 기록에서 비롯한 별개 사안이므로,
        # 재생성 품질 판정에 섞으면 안 된다.
        regenerated_ids = set()
        for b in old_blocks:
            src = sources.get(b.identifier.lower())
            if find_fabrication_markers([b.text]) or (
                src and ungrounded_evidence(b.text, src)
            ):
                regenerated_ids.add(b.identifier)

        violations = []          # 재생성 블록의 위반 (복구 품질 문제)
        legacy_issues = []       # 보존 블록의 위반 (기존 기록 문제)
        checked = 0
        for b in new_blocks:
            src = sources.get(b.identifier.lower())
            if not src:
                continue
            evidence = extract_evidence(b.text)
            if not evidence:
                continue
            checked += 1
            for item in evidence:
                bad = ungrounded_numbers(item, src)
                if not bad:
                    continue
                target = violations if b.identifier in regenerated_ids else legacy_issues
                target.append((b.identifier, sorted(bad), item[:60]))

        # 사용자 💭 보존 확인
        old_thoughts = {b.identifier: b.thought for b in old_blocks if b.thought}
        lost = [
            ident for ident, th in old_thoughts.items()
            if th not in new_text
        ]

        print(f"=== {name} ===")
        print(f"  블록: 구버전 {len(old_blocks)} → 신규 {len(new_blocks)}")
        print(f"  식별자 보존: {'OK' if preserved else '누락 있음'} / 순서: {'OK' if order_ok else '변경됨'}")
        print(f"  창작 표지: 구버전 {old_markers} → 신규 {new_markers}"
              f"{'  (완전 제거)' if new_markers == 0 else '  ⚠ 잔존'}")
        print(f"  근거 대조: {checked}블록 검사")
        print(f"    재생성 블록 위반: {len(violations)}건  (복구 품질)")
        for ident, bad, item in violations[:5]:
            print(f"      ⚠ [{ident}] 원문에 없는 숫자 {bad}: {item}")
        print(f"    보존 블록 위반: {len(legacy_issues)}건  (과거 기록 문제, 복구 범위 밖)")
        for ident, bad, item in legacy_issues[:5]:
            print(f"      · [{ident}] 원문에 없는 숫자 {bad}: {item}")
        print(f"  사용자 💭 유실: {len(lost)}건 {lost[:5] if lost else ''}")
        print(f"  신규 문서 ID: {new_id}")
        print()

        summary.append((name, new_markers == 0 and not violations and preserved and not lost))

    print("=== 종합 ===")
    for name, ok in summary:
        print(f"  {name:22} {'통과' if ok else '확인 필요'}")
finally:
    docs.close()
