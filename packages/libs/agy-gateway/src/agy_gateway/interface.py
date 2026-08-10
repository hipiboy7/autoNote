"""agy 워커 실행 계약.

워커(worker)는 '모델 + 추론 강도 + 역할 프롬프트 + 출력 스키마' 를 묶은 1회성 무상태
실행 단위다. agy 의 커스터마이징 체계에는 subagent 타입이 없으므로(Rules/Skills/
Plugins/Hooks/MCP 뿐), 역할별 전문화는 이 조합으로 구현하고 오케스트레이션은
파이썬 프로그램이 담당한다.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class WorkerSpec:
    """역할별 워커 정의. 값은 config/환경변수로 주입한다 (하드코딩 금지 룰)."""

    name: str
    model: str
    effort: Optional[str] = None          # low | medium | high (미지정 시 모델 기본값)
    timeout_seconds: int = 600
    batch_size: int = 1                   # 1회 호출에 묶을 항목 수
    max_prompt_chars: int = 12000          # Windows 명령행 길이 한계 대비 안전선

    def with_model(self, model: str) -> "WorkerSpec":
        return WorkerSpec(
            name=self.name,
            model=model,
            effort=self.effort,
            timeout_seconds=self.timeout_seconds,
            batch_size=self.batch_size,
            max_prompt_chars=self.max_prompt_chars,
        )


@dataclass
class WorkerResult:
    """agy 응답 엔벨로프에서 추출한 결과."""

    structured: Any
    raw_response: str = ""
    usage: Dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0
    num_turns: int = 0
    conversation_id: str = ""
    model: str = ""
    worker: str = ""

    @property
    def total_tokens(self) -> int:
        return int(self.usage.get("total_tokens", 0) or 0)


class WorkerUnavailableError(RuntimeError):
    """워커 실행이 실패했음을 알린다.

    이 예외가 발생하면 호출자는 **어떤 내용도 문서에 기록하지 않고** 중단해야 한다.
    원문에 근거하지 않은 내용을 대신 생성해 채우는 것은 AGENTS.md 절대 규칙 위반이다.
    """


class WorkerRunner(ABC):
    @abstractmethod
    def run(
        self,
        spec: WorkerSpec,
        prompt: str,
        schema: Optional[dict] = None,
    ) -> WorkerResult:
        """워커를 1회 실행한다.

        schema 가 주어지면 구조화 출력을 강제하고 검증된 객체를 structured 에 담는다.
        실패 시 WorkerUnavailableError 를 던진다.
        """
        raise NotImplementedError
