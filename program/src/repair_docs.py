"""과거 창작 폴백으로 오염된 테마/종목 문서를 원문 기준으로 재생성한다.

배경: LLM API 키가 `dummy-key` 인 상태로 배치가 돌던 기간에, 제거된 규칙 기반 폴백이
원문과 무관한 고정 문구를 생성해 문서에 기록했다(실측: 반도체사이클 282블록 중
199블록, 시장심리·수급 91블록 중 30블록). 원문은 `00_원문보관` 문서에 100% 남아 있어
재생성이 가능하다.

제자리 치환을 쓰지 않는 이유: 창작 문구는 199개 블록에 **완전히 동일한 문자열**로
들어가 있다. Google Docs replaceAllText 는 일치하는 모든 곳을 바꾸므로 강행하면
무관한 블록까지 오염된다. 그래서 새 문서로 재구성한 뒤 매핑을 교체한다.

보존 원칙:
  * 정상 블록은 **원문 그대로** 옮긴다 (재분석하지 않는다).
  * 사용자가 적은 `▶ 💭 내 생각` 은 오염 블록이라도 그대로 보존한다.
  * 블록 순서를 유지한다.
"""

import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from analyzer import ResearchAnalyzer
from analyzer.grounding import find_fabrication_markers
from google_docs_client import GoogleDocsClient
from writer import BLOCK_SEPARATOR, UNRECORDED, format_research_block

HEADER = re.compile(
    r"■\s*\[(?P<ident>[^\]]+)\]\s*(?P<date>\d{4}-\d{2}-\d{2})\s*\|\s*(?P<title>[^\n]*)"
)
THOUGHT = re.compile(r"▶\s*💭\s*내\s*생각:\s*([^\n]*)")
SOURCE_LINE = re.compile(r"▶\s*출처:\s*[^\n]*\n")


@dataclass
class Block:
    identifier: str
    date_str: str
    title: str
    text: str
    fabricated: bool = False
    thought: str = ""


@dataclass
class RepairResult:
    doc_name: str
    new_doc_id: str = ""
    total_blocks: int = 0
    fabricated: int = 0
    repaired: int = 0
    unrepairable: List[str] = field(default_factory=list)
    preserved_thoughts: int = 0


def _thought_of(block_text: str) -> str:
    """블록에서 사용자가 적은 생각만 뽑는다 (미기록 placeholder 는 제외)."""
    match = THOUGHT.search(block_text)
    if not match:
        return ""
    value = match.group(1).strip()
    if not value or value.startswith(UNRECORDED):
        return ""
    return value


def parse_blocks(doc_text: str) -> List[Block]:
    """문서를 구분선 기준으로 블록 단위 파싱한다."""
    blocks: List[Block] = []
    for chunk in doc_text.split(BLOCK_SEPARATOR):
        if not chunk.strip():
            continue
        header = HEADER.search(chunk)
        if not header:
            # 문서 제목/머리말 등 헤더가 없는 조각은 그대로 보존한다
            blocks.append(Block(identifier="", date_str="", title="", text=chunk))
            continue
        blocks.append(
            Block(
                identifier=header.group("ident").strip(),
                date_str=header.group("date"),
                title=header.group("title").strip(),
                text=chunk,
                fabricated=bool(find_fabrication_markers([chunk])),
                thought=_thought_of(chunk),
            )
        )
    return blocks


def parse_original_sources(doc_texts: Sequence[str]) -> Dict[str, str]:
    """원문보관 문서들에서 식별자 → 원문 본문 맵을 만든다."""
    sources: Dict[str, str] = {}
    for doc_text in doc_texts:
        for chunk in doc_text.split(BLOCK_SEPARATOR):
            header = HEADER.search(chunk)
            if not header:
                continue
            ident = header.group("ident").strip().lower()
            body = chunk[header.end():]
            body = SOURCE_LINE.sub("", body, count=1)
            body = body.strip()
            if body:
                sources[ident] = body
    return sources


class DocRepairer:
    def __init__(
        self,
        docs_client: GoogleDocsClient,
        analyzer: ResearchAnalyzer,
        drive_folder_id: str = "",
        new_title_suffix: str = " (재생성)",
    ):
        self.docs_client = docs_client
        self.analyzer = analyzer
        self.drive_folder_id = drive_folder_id
        self.new_title_suffix = new_title_suffix

    def repair(
        self,
        doc_name: str,
        doc_id: str,
        original_doc_ids: Sequence[str],
        dry_run: bool = False,
    ) -> RepairResult:
        doc_text = self.docs_client.read_text(doc_id)
        if not doc_text:
            raise RuntimeError(f"{doc_name}: 문서를 읽지 못했습니다 (id={doc_id}).")

        blocks = parse_blocks(doc_text)
        fabricated = [b for b in blocks if b.fabricated]
        result = RepairResult(
            doc_name=doc_name,
            total_blocks=len([b for b in blocks if b.identifier]),
            fabricated=len(fabricated),
            preserved_thoughts=len([b for b in fabricated if b.thought]),
        )

        print(
            f"[DocRepairer] {doc_name}: 블록 {result.total_blocks}개 중 "
            f"오염 {result.fabricated}개 (사용자 💭 보존 대상 {result.preserved_thoughts}개)"
        )
        if dry_run or not fabricated:
            return result

        sources = parse_original_sources(
            [self.docs_client.read_text(d) for d in original_doc_ids]
        )

        # 재분석 대상 준비: 식별자에서 숫자만 뽑아 message_id 로 쓴다
        targets = []
        id_map: Dict[int, Block] = {}
        for block in fabricated:
            source = sources.get(block.identifier.lower())
            digits = re.sub(r"\D", "", block.identifier)
            if not source or not digits:
                result.unrepairable.append(block.identifier)
                continue
            msg_id = int(digits)
            id_map[msg_id] = block
            targets.append((msg_id, source))

        if result.unrepairable:
            print(
                f"[DocRepairer] 원문을 찾지 못해 재생성할 수 없는 블록: "
                f"{result.unrepairable}",
                file=sys.stderr,
            )

        reports = self.analyzer.analyze_batch(targets) if targets else {}

        # 새 본문 조립 (순서 유지)
        rebuilt: List[str] = []
        for block in blocks:
            if not block.identifier:
                rebuilt.append(block.text.strip())
                continue

            digits = re.sub(r"\D", "", block.identifier)
            report = reports.get(int(digits)) if digits else None
            if block.fabricated and report is not None:
                rebuilt.append(
                    format_research_block(
                        identifier=block.identifier,
                        date_str=block.date_str,
                        title=block.title,
                        report=report,
                        thought=block.thought,
                    ).strip()
                )
                result.repaired += 1
            else:
                # 정상 블록과 재생성 실패 블록은 원문 그대로 보존
                rebuilt.append(f"{BLOCK_SEPARATOR}\n{block.text.strip()}")
                if block.fabricated and block.identifier not in result.unrepairable:
                    result.unrepairable.append(block.identifier)

        new_doc_id = self.docs_client.create_doc(
            f"{doc_name}{self.new_title_suffix}", folder_id=self.drive_folder_id
        )
        if not new_doc_id:
            raise RuntimeError(f"{doc_name}: 새 문서를 생성하지 못했습니다.")

        # Docs 요청 크기를 고려해 나눠 기록한다
        buffer = ""
        for piece in rebuilt:
            candidate = f"{buffer}\n{piece}\n" if buffer else f"{piece}\n"
            if len(candidate) > 8000 and buffer:
                self.docs_client.append_text(new_doc_id, buffer)
                buffer = f"{piece}\n"
            else:
                buffer = candidate
        if buffer:
            self.docs_client.append_text(new_doc_id, buffer)

        self.docs_client.apply_sepia_theme(new_doc_id)
        result.new_doc_id = new_doc_id

        print(
            f"[DocRepairer] {doc_name}: 재생성 {result.repaired}개 완료 → 새 문서 {new_doc_id}"
        )
        if result.unrepairable:
            print(
                f"[DocRepairer] 재생성 실패(원문 보존됨): {sorted(set(result.unrepairable))}",
                file=sys.stderr,
            )
        return result
