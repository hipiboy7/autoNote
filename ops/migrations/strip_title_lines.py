"""본문에 잘못 기록된 'Document Title:' 줄을 제거한다 (일회성 정리).

경위: `docs.getText` 는 응답 앞에 'Document Title: ...' 머리말을 붙인다. 이것은 본문이
아니라 도구가 덧붙인 메타데이터인데, 문서 재구성(복구·되쓰기) 과정에서 본문 조각으로
취급되어 실제 본문에 기록됐다. read_text 가 머리말을 떼도록 고쳤으므로 재발하지
않지만, 이미 기록된 줄은 이 스크립트로 지운다.

사용법:
    python ops/migrations/strip_title_lines.py --dry-run
    python ops/migrations/strip_title_lines.py
    python ops/migrations/strip_title_lines.py 금리 환율·매크로
"""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

os.chdir(_ROOT)

from google_docs_client.mcp_client import MCPGoogleDocsClient
from autonote.restore_docs import CHUNK_CHARS
from writer import BLOCK_SEPARATOR

TITLE_MARKER = "Document Title:"

TARGETS = {
    "반도체사이클": "1UeI5DBboBk-Tedvu4G6ucOuZdxJAtkRg55N_VMV4U-s",
    "시장심리·수급": "1k2vvUvzgl6iW08MGbY9oehc2zBnKtPAi-z6OqrwaSoA",
    "환율·매크로": "18lU0RmfkofcRuosdPn664x4AG8DSO12gvsDqLUi2eKE",
    "금리": "1PK88LaNGhBrxGC0xFGX4tkTPUU15kqzuOhK5SzQ74Eo",
    "방산·방위산업": "1AvDpijnMrsi92m5OGg2APcg8xfniGA-jiXkiA6ygIJQ",
    "한화에어로스페이스": "1F2YJCAtqznASlhY9351UA0qX-81bw2hbrqQM6qrrd9I",
    "에이유브랜즈": "1cUfOHw0OAV0Wwpi5qM4aR3BSi2-H76aV7F8L9RtK-ek",
    "에이피알": "1zC3oDw1kbP651QjsPHztgzo7aCrZr7qPczddgdwmEvY",
}


def clean_body(text: str) -> str:
    """'Document Title:' 줄만 제거하고, 머리말 앞의 불필요한 구분선도 정리한다."""
    kept = [line for line in text.split("\n") if not line.strip().startswith(TITLE_MARKER)]
    body = "\n".join(kept)

    parts = [p for p in body.split(BLOCK_SEPARATOR) if p.strip()]
    if not parts:
        return ""

    out = []
    for index, part in enumerate(parts):
        stripped = part.strip()
        if index == 0 and "■" not in stripped:
            # 문서 머리말은 구분선 없이 맨 앞에 둔다 (원래 형태)
            out.append(stripped)
        else:
            out.append(f"{BLOCK_SEPARATOR}\n{stripped}")
    return "\n".join(out) + "\n"


def chunks(text: str):
    parts = [p for p in text.split(BLOCK_SEPARATOR) if p.strip()]
    buffer = ""
    for index, part in enumerate(parts):
        block = part.strip() + "\n" if index == 0 and "■" not in part else \
            f"{BLOCK_SEPARATOR}\n{part.strip()}\n"
        if buffer and len(buffer) + len(block) > CHUNK_CHARS:
            yield buffer
            buffer = block
        else:
            buffer += block
    if buffer:
        yield buffer


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    names = args or list(TARGETS)

    docs = MCPGoogleDocsClient()
    failures = 0
    try:
        for name in names:
            doc_id = TARGETS.get(name)
            if not doc_id:
                print(f"[Strip] 알 수 없는 문서 키: {name}", file=sys.stderr)
                failures += 1
                continue

            before = docs.read_text(doc_id)
            hits = sum(
                1 for line in before.split("\n")
                if line.strip().startswith(TITLE_MARKER)
            )
            if not hits:
                print(f"[Strip] {name}: 정리 대상 없음")
                continue

            cleaned = clean_body(before)
            print(f"[Strip] {name}: 'Document Title:' 줄 {hits}개 제거 "
                  f"({len(before)}자 → {len(cleaned)}자)")
            if dry_run:
                continue

            if not docs.clear_body(doc_id):
                print(f"[Strip] {name}: 본문 비우기 실패 — 건너뜁니다.", file=sys.stderr)
                failures += 1
                continue

            wrote = all(docs.append_text(doc_id, chunk) for chunk in chunks(cleaned))
            if not wrote:
                print(f"[Strip] {name}: 되쓰기 실패 — 문서를 확인하십시오.", file=sys.stderr)
                failures += 1
                continue

            docs.apply_sepia_theme(doc_id)

            after = docs.read_text(doc_id)
            remaining = sum(
                1 for line in after.split("\n")
                if line.strip().startswith(TITLE_MARKER)
            )
            blocks_after = len([p for p in after.split(BLOCK_SEPARATOR) if p.strip()])
            status = "OK" if remaining == 0 else f"잔존 {remaining}개"
            print(f"[Strip] {name}: 완료 ({status}, 블록 {blocks_after})")
            if remaining:
                failures += 1
    finally:
        docs.close()

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
