"""역할별 agy 워커 로스터 (단일 출처).

agy 에는 subagent 정의 기능이 없다 (커스터마이징 타입은 Rules/Skills/Plugins/
Hooks/MCP 뿐이며 `agy agents` 는 비어 있다). 따라서 '서브에이전트' 는
**모델 + 추론 강도 + 역할 프롬프트 + 출력 스키마** 를 묶은 1회성 무상태 워커로
구현하고, 오케스트레이션·배치·검증은 파이썬 프로그램이 담당한다.

모델 선정 근거는 2026-08-05 실측이다 (동일 프롬프트/동일 스키마, 단건 호출):

| 모델                      | 시간  | input  | output | total  | 판정                          |
|---------------------------|-------|--------|--------|--------|-------------------------------|
| gemini-3.1-pro-high       | 22.7s | 21.9k  | 1,395  | 23.3k  | 정확·최소출력 → extract 기본  |
| gemini-3.5-flash-medium   | 22.7s | 20.7k  | 1,510  | 22.2k  | 대량 처리 → triage/repair     |
| gemini-3.5-flash-low      | 25.0s | 20.7k  | 1,851  | 22.5k  | medium 보다 느림 → 미채택     |
| gemini-3.6-flash-high     | 27.5s | 26.7k  | 4,104  | 30.8k  | 장황 → 미채택                 |
| claude-opus-4-6-thinking  | 55.4s | 26.0k  | 1,938  | 27.9k  | 논리압축 최고 → review        |
| gpt-oss-120b-medium       | 82.0s | 319.7k | 11.5k  | 331.2k | 토큰 15배·오분류 → **금지**   |

핵심 제약: 호출 1건당 input 약 20.7k 토큰이 고정 오버헤드다. 따라서 건별 호출을
피하고 batch_size 만큼 묶어 amortize 한다 (배치 조립은 프로그램 책임).
"""

import os
import sys
from typing import Dict

from agy_gateway import WorkerSpec

# 실측으로 사용 가능성이 확인된 모델 (agy models 출력 기준)
KNOWN_MODELS = (
    "gemini-3.6-flash-high", "gemini-3.6-flash-medium", "gemini-3.6-flash-low",
    "gemini-3.5-flash-high", "gemini-3.5-flash-medium", "gemini-3.5-flash-low",
    "gemini-3.1-pro-high", "gemini-3.1-pro-low",
    "claude-sonnet-4-6", "claude-opus-4-6-thinking",
    "gpt-oss-120b-medium",
)

# 실측 결과 배제된 모델과 사유 (되살릴 때는 근거를 다시 측정할 것)
BLOCKED_MODELS = {
    "gpt-oss-120b-medium": (
        "input 319.7k 토큰(다른 모델의 15배), 82초, 테마 오분류, "
        "main_claim 에 내용 대신 메타서술 생성, 도구로 파일 생성 시도"
    ),
}

DEFAULT_ROSTER: Dict[str, WorkerSpec] = {
    # 리서치 여부 판별 — 출력이 작아 배치를 크게 잡는다
    "triage": WorkerSpec(
        name="triage",
        model="gemini-3.5-flash-medium",
        batch_size=15,
        timeout_seconds=420,
        max_prompt_chars=11000,
    ),
    # 핵심 논리·근거·리스크 추출 + 테마 분류 — 정확도가 가장 중요
    "extract": WorkerSpec(
        name="extract",
        model="gemini-3.1-pro-high",
        batch_size=4,
        timeout_seconds=600,
        max_prompt_chars=11000,
    ),
    # 주간리뷰 종합·관점 변화 추적 — 주 1회, 품질 최우선
    "review": WorkerSpec(
        name="review",
        model="claude-opus-4-6-thinking",
        batch_size=1,
        timeout_seconds=900,
        max_prompt_chars=24000,
    ),
    # 과거 오염 블록 재생성 — 대량 처리
    "repair": WorkerSpec(
        name="repair",
        model="gemini-3.5-flash-medium",
        batch_size=4,
        timeout_seconds=600,
        max_prompt_chars=11000,
    ),
}


def _env_name(role: str, field: str) -> str:
    return f"AGY_WORKER_{role.upper()}_{field.upper()}"


def _int_env(name: str, fallback: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return fallback
    try:
        return max(1, int(raw))
    except ValueError:
        print(f"[worker_roster] {name}={raw!r} 를 정수로 읽을 수 없어 기본값 {fallback} 사용",
              file=sys.stderr)
        return fallback


def load_roster() -> Dict[str, WorkerSpec]:
    """환경변수 오버라이드를 적용한 로스터를 반환한다 (하드코딩 금지 룰).

    오버라이드 키: AGY_WORKER_<ROLE>_MODEL / _EFFORT / _BATCH_SIZE / _TIMEOUT_SECONDS
    """
    roster: Dict[str, WorkerSpec] = {}
    for role, default in DEFAULT_ROSTER.items():
        model = os.getenv(_env_name(role, "MODEL"), default.model)
        effort = os.getenv(_env_name(role, "EFFORT"), default.effort or "") or None
        batch = _int_env(_env_name(role, "BATCH_SIZE"), default.batch_size)
        timeout = _int_env(_env_name(role, "TIMEOUT_SECONDS"), default.timeout_seconds)

        if model in BLOCKED_MODELS:
            print(
                f"[worker_roster] 경고: {role} 워커에 배제 모델 {model} 이 지정되었습니다. "
                f"사유: {BLOCKED_MODELS[model]}",
                file=sys.stderr,
            )
        elif model not in KNOWN_MODELS:
            print(
                f"[worker_roster] 경고: {role} 워커의 모델 {model} 은 실측 목록에 없습니다. "
                "`agy models` 로 확인하십시오.",
                file=sys.stderr,
            )

        roster[role] = WorkerSpec(
            name=role,
            model=model,
            effort=effort,
            timeout_seconds=timeout,
            batch_size=batch,
            max_prompt_chars=default.max_prompt_chars,
        )
    return roster
