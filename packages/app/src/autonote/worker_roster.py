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

추가 실측 (2026-08-18, agy 1.1.14). extract 워크로드 = 동일 프롬프트/스키마로
메시지 2건 배치:

| 모델                    | 시간   | input | output | total | 판정                        |
|-------------------------|--------|-------|--------|-------|-----------------------------|
| gemini-3.7-flash-high   | 30.8s  | 37.9k | 3.5k   | 41.4k | 채택 → extract/repair       |
| gemini-3.1-pro-high     | 105.9s | 27.0k | 9.6k   | 36.6k | 3.4배 느림·출력 부실 → 강등 |

triage 워크로드(2건). 속도·토큰만 보면 교체 이득이 없었다:

| 모델                    | 시간  | total | 판정                        |
|-------------------------|-------|-------|-----------------------------|
| gemini-3.5-flash-medium | 20.9s | 16.4k | 2.6초 빠르고 토큰도 소폭 적음 |
| gemini-3.7-flash-high   | 23.5s | 16.6k | 채택 (사용자 결정)          |

triage 는 실측 우위가 아니라 **사용자 결정으로** 3.7-flash-high 를 쓴다 (2026-08-18).
판별 정확도는 두 모델 모두 동일했고(983 비리서치·987 리서치), 차이는 23.5s 대 20.9s
수준이라 배치 전체에 미치는 영향이 작다. 되돌리려면 AGY_WORKER_TRIAGE_MODEL 로
덮어쓰거나 이 값을 gemini-3.5-flash-medium 으로 되돌리면 된다.

실메시지 4건(983·985·986·987) 배치로 근거 검증까지 통과시킨 결과가 판단의 근거다.
  * gemini-3.1-pro-high  — 93.2s. 4건 전부 risks 가 0개이고 critical_question 이
    빈 문자열로 왔다. 근거도 '8월' '7월' '28년' 같은 의미 없는 조각이었다.
  * gemini-3.7-flash-high — 56.7s. 근거를 4개씩 원문 문구 그대로 집었고 risks 와
    critical_question 도 채웠다. 근거가 없는 인사말(983)에는 규칙대로 빈 배열을 뒀다.

2026-10-13 무렵 agy 가 모델 라인업을 전면 교체해 위 모델이 모두 사라졌다.
재실측 (2026-10-19, agy 1.4.2, 실메시지 1900~2003, 운영 ResearchTriage/ResearchAnalyzer
그대로 — 근거 검증 포함).

triage 워크로드(100건, 6호출):

| 모델                    | 누적 시간 | total  | 리서치 판정 | 판정                    |
|-------------------------|-----------|--------|-------------|-------------------------|
| gemini-3.8-flash-high   | 1485s     | 220.5k | 60          | 2.8배 느림 → 미채택     |
| gemini-3.8-flash-medium | 530s      | 112.9k | 62          | 채택 → triage           |

판정 차이 2건(1905·1965)은 둘 다 경계 사례(짧은 사실 메모·스터디 계획 공지)였고
나머지 60건은 완전히 일치했다. 08-18 의 "triage=extract 와 같은 모델" 사용자 결정은
extract 가 flash 계열을 떠나면서 유지하지 않았다 (argon 을 triage 에 쓸 근거 미측정).

extract 워크로드(1900·1902·1903·1904, 1호출, 세 모델 모두 근거 검증 1회 통과):

| 모델                  | 시간   | input | output | total | 판정                              |
|-----------------------|--------|-------|--------|-------|-----------------------------------|
| gemini-3.8-flash-high | 255.8s | 73.2k | 17.6k  | 90.8k | 1900·1902 evidence 0개 → 부실     |
| gemini-4-argon-medium | 100.4s | 22.5k | 3.2k   | 25.7k | 채택 → extract/repair             |
| gemini-4-argon-high   | 169.6s | 36.4k | 5.3k   | 41.7k | evidence 동일, 느리고 비쌈 → 미채택 |

review: claude-opus-5-5-high 산문 호출 정상(31.8s, 17.5k). 주간리뷰 실데이터로는 미측정.

핵심 제약: 호출 1건당 input 약 20.7k 토큰이 고정 오버헤드다. 따라서 건별 호출을
피하고 batch_size 만큼 묶어 amortize 한다 (배치 조립은 프로그램 책임).
"""

import os
import sys
from typing import Dict

from agy_gateway import WorkerRunner, WorkerSpec, WorkerUnavailableError

# 실측으로 사용 가능성이 확인된 모델 (2026-10-19 agy 1.4.2 `agy models` 출력 기준)
KNOWN_MODELS = (
    "gemini-4-argon-high", "gemini-4-argon-medium", "gemini-4-argon-low",
    "gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low",
    "claude-opus-5-5-high", "claude-opus-5-5-medium", "claude-opus-5-5-low",
    "claude-sonnet-5-5-high", "claude-sonnet-5-5-medium", "claude-sonnet-5-5-low",
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
        model="gemini-3.8-flash-medium",
        batch_size=15,
        timeout_seconds=420,
        max_prompt_chars=11000,
    ),
    # 핵심 논리·근거·리스크 추출 + 테마 분류 — 정확도가 가장 중요
    "extract": WorkerSpec(
        name="extract",
        model="gemini-4-argon-medium",
        batch_size=4,
        timeout_seconds=600,
        max_prompt_chars=11000,
    ),
    # 주간리뷰 종합·관점 변화 추적 — 주 1회, 품질 최우선
    "review": WorkerSpec(
        name="review",
        model="claude-opus-5-5-high",
        batch_size=1,
        timeout_seconds=900,
        max_prompt_chars=24000,
    ),
    # 과거 오염 블록 재생성 — ResearchAnalyzer 를 그대로 쓰므로 extract 와 동일 워크로드다.
    # 같은 스키마를 다루는 만큼 모델도 extract 와 맞춘다 (2026-08-18·10-19 실측 근거).
    "repair": WorkerSpec(
        name="repair",
        model="gemini-4-argon-medium",
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


class RosterModelError(RuntimeError):
    """로스터가 지정한 모델을 agy 가 더 이상 제공하지 않는다."""


def verify_roster_models(roster: Dict[str, WorkerSpec], runner: WorkerRunner) -> None:
    """배치 시작 전에 로스터의 모델이 모두 실행 가능한지 확인한다.

    agy 는 예고 없이 모델 라인업을 교체한다. 2026-10-13~19 에는 로스터의 모델이
    전부 사라졌는데, triage 는 조용히 폴백하고 review 는 stderr 한 줄로 끝나
    추출 단계에서야 실패가 드러났다. 여기서 한 번에 실패시켜 알림에 원인과
    대체 후보를 싣는다.

    목록을 얻지 못하면 막지 않는다 (일시적 네트워크 문제로 멀쩡한 배치를 세우지 않는다).
    그 경우 워커 호출 단계가 실제 오류를 보고한다.
    """
    try:
        available = runner.list_models()
    except WorkerUnavailableError as e:
        print(f"[worker_roster] 모델 목록 확인 실패, 사전 점검을 건너뜁니다: {e}",
              file=sys.stderr)
        return
    if available is None:
        return

    missing = {role: spec.model for role, spec in roster.items() if spec.model not in available}
    if missing:
        listed = ", ".join(f"{role}={model}" for role, model in missing.items())
        raise RosterModelError(
            f"agy 가 제공하지 않는 모델이 로스터에 있습니다: {listed}. "
            f"사용 가능: {', '.join(available)}. "
            "worker_roster.DEFAULT_ROSTER 를 실측 후 갱신하거나, 당장은 "
            "AGY_WORKER_<ROLE>_MODEL 환경변수로 덮어쓰십시오."
        )
