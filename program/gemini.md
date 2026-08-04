# llm-automation 작업 가이드 (gemini 용)

이 파일은 본 레포에서 작업하는 모든 협업자(사람 + gemini) 가 따라야 하는 공식 룰을 정의한다.
변경 시 PR 로 합의하고, 모든 모듈/컨테이너 작업은 이 규칙을 준수한다.

---

## 1. 프로젝트 그라운드 룰 (필수)
1. **컨테이너/마이크로서비스 단위 독립성**
   - 각 컨테이너(또는 마이크로서비스)는 다른 컨테이너에 강결합 없이 독립적으로 빌드·기동·교체 가능해야 한다.
   - 모듈 간 의존은 **인터페이스(추상)** 로만. 구체 구현을 직접 import 하지 않는다.
2. **컨테이너 단위 test 및 release**
   - 빌드/테스트/릴리스 파이프라인을 컨테이너 단위로 분리한다.
   - 한 서비스 변경이 다른 서비스의 재배포를 강제해서는 안 된다.
   - 각 서비스는 자체 `Dockerfile`, `tests/`, 빌드테스트 스크립트를 가진다.
3. **TDD + 코드 커버리지  95%**
   - 소스 코드가 존재하는 모든 모듈은 **테스트를 먼저 작성(red-green-refactor)** 하고 구현한다.
   - 추상화 계층(예: `LLMGateway`, `AgentRunner`) 을 적극 활용해 백엔드 교체 가능성을 보장한다.
   - 라인/브랜치 커버리지 95% 이상 유지. 미달 시 `테스트결과서.md` 에 사유 명시.
4. **SOLID 원칙 준수**
   - SRP / OCP / LSP / ISP / DIP 준수.
5. **문서 선행  구현  테스트 결과서**
   - 작업 순서: **요구사항 정의서 ➔ 설계서 ➔ 구현 ➔ 테스트 결과서**.
   - 구현이 먼저 진행되지 않는다. 요구사항/설계가 변경되면 문서를 먼저 업데이트한 후 코드에 반영한다.
   - 각 모듈/서비스는 자체 `docs/` 또는 루트에 위 4종 산출물을 둔다.
6. **`.env` 는 항상 `sample.env` 로 현행화**
   - 실제 값이 들어간 `.env` 는 commit 금지 (`.gitignore` 필수).
   - 같은 키 목록·주석·예시값을 담은 **`sample.env` 를 항상 최신 상태로 유지하고 commit** 한다.
   - `.env` 에 키를 추가/삭제/이름변경할 때 **동일 PR/커밋에서 `sample.env` 도 함께 갱신**한다.
   - 누구든 `cp sample.env .env` 한 줄로 환경을 복원할 수 있어야 한다.
   - 적용 범위: 루트, 각 서비스/라이브러리, infra/ 등 `.env` 를 사용하는 모든 위치.
7. **하드코딩 금지  의미 있는 상수는 모두 환경변수로**
   - 의미 있는 상수(host/port/URL, 모델명, API key·시크릿, 타임아웃, 재시도 횟수, 임계치, 큐/컬렉션 이름, 경로, 기능 플래그 등) 는 **소스 코드에 리터럴로 박지 않는다**.
   - 런타임 값은 `.env` 로, 구조적 설정은 `config/*.yaml` 로 주입하며, 코드는 **`Settings`/`Config` 객체(예: `pydantic-settings`) 를 통해서만** 값을 참조한다.
   - 환경변수를 추가/변경하면 같은 PR에서 `sample.env`, 해당 모듈 `docs/설계서.md`의 "설정 항목" 표, (필요 시) `docker-compose`/`stack` 환경변수 매핑을 함께 갱신한다.
   - **테스트 시:** 환경변수는 `pytest` fixture(예: `monkeypatch.setenv`) 또는 `.env.test`로 주입한다. 테스트 코드 내에서도 하드코딩을 금지한다.
   - **예외 (하드코딩 허용):** 수학 상수, 명백한 프로토콜 상수(예: HTTP 상태코드 200), 테스트 fixture 값. 매직 넘버는 명명된 상수로 추출한다.

---

## 2. 표준 디렉터리 구조
```
llm-automation/
gemini.md                       # 본 문서
architecture_v2.md              # production 아키텍처 (변경 금지 영역)
architecture_test.md            # 테스트 환경 아키텍처
구현계획.md                     # 단계별 구현 로드맵
docs/
  templates/                    # 요구사항/설계/테스트결과 양식
  P00_요구사항.md               # Phase 단위 산출물 (P{NN}_<종류>.md)
  P00_설계서.md
  P00_테스트결과서.md
  P01_요구사항.md
  ...                           # P00~P06
libs/                           # 다수 서비스가 공유하는 추상화 라이브러리
  <lib-name>/
    pyproject.toml
    src/
    tests/
    docs/{요구사항.md,설계서.md,테스트결과서.md}
    Dockerfile
    README.md
services/                       # 컨테이너화되는 단위
  <service-name>/
    pyproject.toml
    src/
    tests/
    docs/{요구사항.md,설계서.md,테스트결과서.md}
    Makefile                # build/test/lint/coverage 표준 타깃
    README.md
infra/                          # compose / stack / 부팅 스크립트
e2e/                            # 컨테이너 통합 시나리오 테스트
```

각 서비스 `Makefile` 표준 타깃: `make test`, `make coverage`, `make lint`, `make build`, `make image`.

---

## 3. 문서 산출물 규칙
| 단계 | 산출물 | 위치 | 갱신 시점 |
|---|---|---|---|
| 요구사항 (모듈) | `요구사항.md` | 서비스/라이브러리 `docs/` | 작업 착수 전, 범위 변경 시 |
| 설계 (모듈) | `설계서.md` | 서비스/라이브러리 `docs/` | 요구사항 확정 후, 인터페이스 변경 시 |
| 테스트 결과 (모듈) | `테스트결과서.md` | 서비스/라이브러리 `docs/` | 매 release 직전, 커버리지 변동 시 |
| 설계 (Phase) | `P{NN}_설계서.md` | 루트 `docs/` | Phase 요구사항 확정 후 |
| 요구사항 (Phase) | `P{NN}_요구사항.md` | 루트 `docs/` | Phase 진입 시 |
| 테스트 결과 (Phase) | `P{NN}_테스트결과서.md` | 루트 `docs/` | Phase 종료 시 (DoD/모듈 커버리지 요약) |

> Phase 번호는 `구현계획.md` 의 Phase N 과 동일 (Phase 0 ➔ `P00`, Phase 6 ➔ `P06`).

`테스트결과서.md` 필수 항목:
- 실행 환경 (이미지/버전)
- 실행 명령
- 테스트 케이스 수, 통과/실패
- 라인/브랜치 커버리지 (수치)
- 95% 미달 시 사유

---

## 4. 코드/도구 컨벤션 (확정)
| 항목 | 채택 |
|---|---|
| 언어/런타임 | **Python 3.12** |
| 테스트 러너 | **pytest + pytest-cov + pytest-asyncio** |
| 패키지 관리/모노레포 | **uv workspace** (`pyproject.toml` per module) |
| 린터포매터 | **ruff** (포맷린트 통합) |
| 컨테이너 빌드 | 서비스별 **multi-stage Dockerfile**, 베이스 `python:3.12-slim` |
| 타입체크 | **mypy** (`libs/` 는 `strict = true`) |
| 통합 테스트 | docker compose + pytest |
| CI | **단계적 도입 (옵션 B)**  Phase 0~5 는 `Makefile` 만, Phase 6 직전 또는 협업자 합류 시 Gitea/GitHub Actions yaml 추가 (yaml 은 `make <target>` 호출만) |
| 시크릿 관리 | `.env` (gitignore) + 운영 시 Docker Swarm secret. 그라운드 룰 6/7 준수 |

`Makefile` 표준 타깃: `test`, `coverage`, `lint`, `format`, `build`, `image`, `clean`.

---

## 5. 공통 명명 규칙 (production  test 동등)
코드와 manifest 는 **generic 이름** 만 사용한다. production/test 차이는 *값* 으로만 흡수한다.

### 5.1 LLM 추상화 alias
| Alias (코드/yaml 호출명) | 역할 | Production 백엔드 | Test 백엔드 |
|---|---|---|---|
| `chat-llm` | 일반 chat / 코드 주석 (Workflow A) | vLLM Qwen3-Coder 30B | OpenAI (예: gpt-4o) |
| `reasoning-llm` | 분석  추론 (Workflow B) | vLLM GPT-OSS 130B | OpenAI (예: o4-mini) |
| `embedding` | 텍스트 임베딩 | bge-m3 ONNX | OpenAI text-embedding-3-small |

### 5.2 표준 환경변수 (prod/test 공통)
| 변수 | 의미 |
|---|---|
| `CHAT_LLM_BASE_URL` / `_MODEL` / `_API_KEY` | chat-llm 백엔드 설정 |
| `REASONING_LLM_BASE_URL` / `_MODEL` / `_API_KEY` | reasoning-llm 백엔드 설정 |
| `EMBEDDING_BASE_URL` / `_MODEL` / `_API_KEY` / `_DIM` | embedding 백엔드 설정 (DIM 은 Qdrant collection 차원) |
| `OPENAI_API_KEY` | **test 전용 소스 시크릿**. stack.yml 의 `environment:` 매핑 또는 Swarm secret 으로 위 `*_API_KEY` 에 주입 |

### 5.3 규칙
- **코드는 generic 이름만 참조**. backend-specific 이름(`OPENAI_*`, `VLLM_*`, `QWEN_*`) 을 코드/yaml 에 직접 박지 않는다.
- 새로운 backend(예: Anthropic, Azure) 추가 시에도 **alias 는 그대로**, env 값만 교체.
- Prod ➔ Test 매핑 표는 `architecture_test.md` 에서 단일 출처로 관리.
- 검색과 원글 추적을 용이하게 하기 위해, 구글 Docs 기록 시 제목 가장 앞부분에 고유 식별자(`[식별자 접두사 + 메시지ID]`)를 명시한다.
- 각 문서 종류에 따라 고유한 접두사 식별자를 갖는다. 메시지 ID 자릿수가 증가(예: 네 자릿수 이상)하더라도 뒤에 숫자를 이어서 동적으로 붙인다.
  * **원문 보관:** `[r{message_id}]` (예: `[r305]`, `[r1024]`)
  * **테마 노트:** `[t{message_id}]` (예: `[t305]`, `[t1024]`)
  * **종목 노트:** `[s{message_id}]` (예: `[s305]`, `[s1024]`)
  * **프레임 노트:** `[f{message_id}]` (예: `[f305]`, `[f1024]`)

### 5.4 모델 등가성 원칙
- 테스트 환경은 production 의 각 AI 모델을 **유사 성능의 다른 모델** 로 대체한다 (production 자체 호스팅 ➔ OpenAI 등가 모델).
- 목적: GPU/내부 인프라 없이 production 과 **행위적·품질적으로 동등한 검증**.
- 모델 선정 근거, 기본값, 수용 차이(임베딩 차원 등) 는 `architecture_test.md` 에서 단일 출처로 관리.

---

## 6. Production 아키텍처 보호
- `architecture_v2.md` 는 **production 정의 문서** 다. 변경 시 별도 합의 필요.
- 테스트 환경 변경은 `architecture_test.md` 와 각 모듈 `docs/설계서.md` 에 반영한다.

---

## 7. 생각노트 자동 병합 시스템 (하이브리드 배치 규칙)
1. **수집 및 기록 주체 분리:**
   - 텔레그램 메시지 파싱, 중복 검사, Docs 기록 등의 제어 흐름은 **파이썬 프로그램(uv workspace 기반)**이 직접 담당한다.
   - LLM은 파이썬 프로그램의 요청을 받아 핵심 논리, 근거, 리스크, 생각해볼 질문 등을 정제하는 **뇌(API) 역할**만 자율적으로 수행한다.
2. **생각노트 템플릿 자동 제공 (Pre-fill):**
   - 배치가 완료되면 파이썬 스크립트는 `99_생각노트` 구글 문서에 당일 수집된 메시지의 `[식별자]`와 제목, 본문 Docs 링크가 포함된 템플릿 라인을 자동으로 생성해 둔다.
   - 사용자는 템플릿의 `💭 내 생각: ` 우측에 자신의 생각만 타이핑한다.
3. **다음 배치 시 자동 병합 (Merge):**
   - 다음날 배치 시작 시 프로그램은 `99_생각노트`에 사용자가 입력한 생각을 맵 형태로 읽어들인다.
   - 프로그램은 각 본문 Docs에 저장된 고유 식별자(예: `[t305]`, `[s305]`)를 텍스트 검색하여 해당 블록의 `▶ 💭 내 생각:` 부분을 사용자의 코멘트로 자동 치환(replace) 업데이트한다.
   - **대소문자 무시:** 사용자가 생각노트에 적을 때 대소문자 혼용(예: `[t305]`, `[T305]`)을 하더라도 프로그램이 정규식(Regex)을 이용해 대소문자 구분 없이 매칭하여 병합한다.
   - 반영이 완료된 항목은 생각노트에서 성공적으로 제거되거나 아카이브 구역으로 자동 이동한다.
