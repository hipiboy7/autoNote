# 투자 리서치 노트 — Antigravity 운영 규칙

너는 투자 리서치 학습 노트 정리 에이전트다. 텔레그램 채널의 주식 리서치 글(종목 추천이 아닌 분석·판단 공유)을 수집해, 지식이 주제별로 축적되도록 구조화하고 Google Docs 문서에 기록한다. **글 요약이 목적이 아니라 논리 추출과 주제별 축적이 목적이다.**

---

## 1. 그라운드 룰 (필수 선행)

**이 프로젝트의 모든 개발·설계·문서 규칙은 [`docs/rules/개발규칙.md`](./docs/rules/개발규칙.md) 를 따른다.** 코드를 만들거나 고치기 전에 반드시 읽을 것. 본 문서(AGENTS.md)는 *운영* 규칙이고, `gemini.md` 는 *개발* 규칙이다. 충돌하면 `gemini.md` 가 우선한다.

`gemini.md` 가 규정하는 핵심 (요약 — 원문이 정본):

| # | 규칙 | 실무 영향 |
|---|---|---|
| 1 | 모듈 독립성, 인터페이스(추상)로만 의존 | 구체 구현을 직접 import 하지 않는다 |
| 2 | 컨테이너 단위 test/release | 한 서비스 변경이 다른 서비스 재배포를 강제하지 않는다 |
| 3 | TDD + 커버리지 95% | 테스트 먼저. 미달 시 `테스트결과서.md` 에 사유 명시 |
| 4 | SOLID | |
| 5 | 요구사항 → 설계 → 구현 → 테스트결과서 | 구현이 문서보다 앞서지 않는다 |
| 6 | `.env` 는 커밋 금지, `sample.env` 항상 현행화 | 키 추가 시 같은 커밋에서 `sample.env` 갱신 |
| 7 | 하드코딩 금지 | 모델명·경로·타임아웃·임계치는 환경변수 또는 config |

---

## 2. 실행 주체와 3계층 아키텍처

**실행의 주체는 Antigravity(agy) 다.** agy 는 상시 가용하므로 배치 기동·예외 판단·문서 복구 같은 지속 작업을 담당한다.

```
Layer 3 · 감독 (agy 대화 세션)
   이 문서의 규칙을 적용받는다. 배치 기동, 예외 상황 판단, 신규 테마 승인,
   실패 보고 해석. 사람의 말을 받아 Layer 1 을 호출한다.
        │
Layer 1 · 프로그램 (파이썬, 토큰 0, 결정론)
   정확성이 필요한 모든 것. 수집, state 체크포인트, 식별자·블록 포맷,
   Docs 읽기·쓰기·치환 및 읽기 검증, 테마→문서 라우팅, 생각노트 병합,
   월별 문서 생성, 프롬프트 배치 조립, 근거 검증, 토큰 사용량 기록.
        │
Layer 2 · agy 워커 (1회성 무상태, 스키마 강제)
   판단만 한다. 역할별 = 모델 + 추론강도 + 역할 프롬프트 + JSON 스키마.
```

**중요: agy 에는 subagent 정의 기능이 없다.** 커스터마이징 타입은 Rules(`AGENTS.md`/`GEMINI.md`) / Skills / Plugins / Hooks / MCP 뿐이고 `agy agents` 는 비어 있다. 따라서 "서브에이전트" 는 위 Layer 2 의 워커 조합으로 구현하며, 정의는 [`packages/app/src/autonote/worker_roster.py`](./packages/app/src/autonote/worker_roster.py) 가 단일 출처다.

### 워커 호출 규약 (실측 검증됨)

```
agy --model <id> [--effort low|medium|high] --output-format json
    --json-schema <스키마> --print <프롬프트>
    --dangerously-skip-permissions --disable-slash-commands
```

* `--json-schema` 는 `--output-format json` 과 함께만 쓸 수 있다.
* 응답 엔벨로프의 **`structured_output`** 을 쓴다. `response` 에는 모델이 산문을 덧붙이는 경우가 있다.
* 스키마에 **enum 을 넣으면 실제로 강제된다.** 분류 필드를 자유 문자열로 두면 존재하지 않는 값이 생성된다(실측: 테마 `'금지'`).
* **호출 1건당 input 약 20.7k 토큰이 고정 오버헤드다.** 건별 호출 금지, 반드시 배치로 묶는다.

---

## 3. 워커 로스터

2026-08-05 실측(동일 프롬프트·동일 스키마) 기준 배치. 변경 시 근거를 다시 측정하고 이 표를 갱신할 것.

| 워커 | 모델 | 배치 | 용도 |
|---|---|---|---|
| `triage` | `gemini-3.5-flash-medium` | 15건/호출 | 리서치 여부 판별 |
| `extract` | `gemini-3.1-pro-high` | 4건/호출 | 핵심 논리·근거·리스크·테마 추출 |
| `review` | `claude-opus-4-6-thinking` | 1회/주 | 주간리뷰·관점 변화 종합 |
| `repair` | `gemini-3.5-flash-medium` | 4건/호출 | 과거 오염 블록 재생성 |

측정값:

| 모델 | 시간 | input | output | total | 판정 |
|---|---|---|---|---|---|
| `gemini-3.1-pro-high` | 22.7s | 21.9k | 1,395 | 23.3k | 정확·최소출력 |
| `gemini-3.5-flash-medium` | 22.7s | 20.7k | 1,510 | 22.2k | 대량 처리 |
| `gemini-3.5-flash-low` | 25.0s | 20.7k | 1,851 | 22.5k | medium 보다 느림 → 미채택 |
| `gemini-3.6-flash-high` | 27.5s | 26.7k | 4,104 | 30.8k | 장황 → 미채택 |
| `claude-opus-4-6-thinking` | 55.4s | 26.0k | 1,938 | 27.9k | 논리 압축 최고 |
| `gpt-oss-120b-medium` | 82.0s | 319.7k | 11.5k | 331.2k | **사용 금지** |

`gpt-oss-120b-medium` 금지 사유: 토큰 15배, 테마 오분류, `main_claim` 에 내용 대신 메타서술 생성, 도구로 파일 생성 시도.

로스터는 환경변수로 덮어쓸 수 있다: `AGY_WORKER_<ROLE>_MODEL` / `_EFFORT` / `_BATCH_SIZE` / `_TIMEOUT_SECONDS`.

---

## 4. 프로그램과 에이전트의 경계

**판단이 아닌 것은 전부 프로그램이 한다.** 아래를 에이전트가 직접 하는 것을 금지한다.

| 프로그램이 담당 (토큰 0) | 이유 |
|---|---|
| 텔레그램 수집, `state.json` 체크포인트 | 결정론적. 중복·유실 차단이 정확성의 핵심 |
| 식별자 생성·파싱, 블록 서식 조립 | 문자열 규칙. LLM 이 틀리면 병합이 깨진다 |
| Docs 읽기·쓰기·치환 + **읽기 검증** | 치환 성공을 확인해야 사용자 생각이 유실되지 않는다 |
| 테마 → 문서 라우팅 | enum 검증 후 매핑. 폴백 금지 |
| **근거 검증** (숫자 대조) | 창작 차단의 실질적 장치. LLM 재확인보다 값싸고 확실하다 |
| 프롬프트 배치 조립·길이 예산 | 고정 오버헤드 amortize. 명령행 길이 한계 방어 |
| 월별 원문보관 문서 생성·등록 | |
| 생각노트 병합 및 처리완료 표시 | |

| 워커(LLM)가 담당 | 이유 |
|---|---|
| 리서치 여부 판별 | 규칙으로는 짧지만 핵심적인 코멘트를 구분할 수 없다 |
| 핵심 주장·근거·논리·리스크·질문 추출 | 판단 |
| 테마 분류 (enum 내에서) | 판단 |
| 주간리뷰 종합·관점 변화 | 판단 |

---

## 5. 설정

- 기본 채널: `피터케이 채널(26년 하반기)` (`-1004421517798`, `TELEGRAM_CHANNEL_ID`)
- 상태 파일: `./var/state.json` — 채널별 마지막 처리 message_id, 마지막 실행 시각, 주간리뷰 주차, 자동 생성된 월별 문서
- 토큰 사용량 로그: `./var/agy_usage.jsonl` (`AGY_USAGE_LOG`)
- 대상 드라이브 폴더: `투자리서치노트_gemini` (`1mHmyPezUsD4NF5AlIbHkzKzf6TymmesH`)
- 작업 히스토리: `./docs/journal/` — **새 세션 시작 시 최신 문서를 읽고 맥락을 이어받을 것**
- 환경변수 전체 목록: [`sample.env`](./sample.env)

### 문서 매핑

| 용도 | Google Docs 문서 |
|---|---|
| 원문 보관 (2026-07) | [00_원문보관 2026-07](https://docs.google.com/document/d/17b8iLt_KxjWZX7On9hnPXCLZtm6fpT8GBgYb_X85PHY/edit) |
| 원문 보관 (2026-08) | [00_원문보관 2026-08](https://docs.google.com/document/d/1gfyz-nJZ5vtcH8enwnxcGgjjpZ6Smoqj0RvVDix5BMI/edit) |
| 테마: 반도체사이클 | [10_테마 반도체사이클](https://docs.google.com/document/d/1UeI5DBboBk-Tedvu4G6ucOuZdxJAtkRg55N_VMV4U-s/edit) |
| 테마: 시장심리·수급 | [10_테마 시장심리·수급](https://docs.google.com/document/d/1k2vvUvzgl6iW08MGbY9oehc2zBnKtPAi-z6OqrwaSoA/edit) |
| 테마: 환율·매크로 | [10_테마 환율·매크로](https://docs.google.com/document/d/18lU0RmfkofcRuosdPn664x4AG8DSO12gvsDqLUi2eKE/edit) |
| 테마: 금리 | [10_테마 금리](https://docs.google.com/document/d/1PK88LaNGhBrxGC0xFGX4tkTPUU15kqzuOhK5SzQ74Eo/edit) |
| 테마: 방산·방위산업 | [10_테마 방산·방위산업](https://docs.google.com/document/d/1AvDpijnMrsi92m5OGg2APcg8xfniGA-jiXkiA6ygIJQ/edit) |
| 종목: 한화에어로스페이스 | [20_종목 한화에어로스페이스](https://docs.google.com/document/d/1F2YJCAtqznASlhY9351UA0qX-81bw2hbrqQM6qrrd9I/edit) |
| 종목: 에이유브랜즈 | [20_종목 에이유브랜즈](https://docs.google.com/document/d/1cUfOHw0OAV0Wwpi5qM4aR3BSi2-H76aV7F8L9RtK-ek/edit) |
| 종목: 에이피알 | [20_종목 에이피알](https://docs.google.com/document/d/1zC3oDw1kbP651QjsPHztgzo7aCrZr7qPczddgdwmEvY/edit) |
| 판단 프레임 | [30_프레임 판단프레임모음](https://docs.google.com/document/d/1wxRABv-jRTSsqpC4-EC2Ty9VUctZ9SBWr_FMIHEQ8sE/edit) |
| 리뷰 (누적) | [40_리뷰](https://docs.google.com/document/d/1ZMxc48JJgrlCmkKmE9SgRH3doRu4yasG3oymyvWDFHs/edit) |
| 생각노트 (사용자 입력창구) | [99_생각노트](https://docs.google.com/document/d/1p0d-bEHdVTzQjRnmXzzzB5HRvUCQSUyktUpksV70TUc/edit) |

`analyzer.THEMES` 와 이 표의 테마 키는 정확히 일치해야 한다. 프로그램이 기동 시 검증하고 불일치면 즉시 종료한다.

---

## 6. 명령

| 명령 | 동작 |
|---|---|
| `일일 배치` | 배치 절차 실행 (스케줄러: 매일 08:50) |
| `업데이트` | 배치 절차 즉시 실행 (누락 복구·수시 수집) |
| `상태` | 정상/이상 판정과 마지막 배치 결과·오류 보고 (`--mode status`) |
| `상태 점검` | 워치독 실행 — 이상이면 알림까지 발송 (`cli/run_health_check.bat`) |
| `주간리뷰` | 주간리뷰 (재)생성 |
| `백필 <from> <to>` | 메시지 ID 구간 재처리 (`state.json` 갱신하지 않음) |
| `문서 복구 <문서명>` | 오염 블록을 원문보관에서 재분석해 새 문서로 재구성 |
| `내 생각 추가: MM-DD (대상 글) — (내용)` | 해당 테마 문서의 `▶ 💭` 줄에 내용 추가 |
| `추가: (텔레그램 글 / URL / 텍스트)` | 기본 채널 외 소스를 동일 체계로 정리. **출처를 반드시 표기** |

### 실행 방법

```powershell
# 일일 배치 (스케줄러가 호출하는 것과 동일)
D:\autoNote\cli\run_daily_batch.bat

# 개별 모드
python -m autonote.main --mode status
python -m autonote.main --mode batch
python -m autonote.main --mode review
python -m autonote.main --mode merge
python -m autonote.main --mode backfill --from-id 700 --to-id 750
```

---

## 7. 배치 절차

1. **생각 병합** — `99_생각노트` 의 사용자 의견을 본문 Docs 에 병합. 치환 검증 통과분만 `_처리완료` 표시.
2. **수집** — `state.json` 의 `last_message_id` 이후 전부 수집. 며칠 누락됐어도 따라잡는다.
3. **판별** — 프로그램이 확실한 것(본문 없음·링크만·광고/공지)을 제외하고, 나머지는 `triage` 워커가 배치 판별.
4. **추출** — 리서치 판정분을 `extract` 워커가 배치 분석. **근거 검증을 통과해야 통과.**
5. **기록** — 원문보관(월별 자동 라우팅) → 테마/종목 문서 → 생각노트 Pre-fill. 메시지 단위 체크포인트.
6. **주간리뷰** — 그 주 첫 배치면 지난주 리뷰 생성. **신규 메시지가 없는 날에도 실행된다.**
7. **상태 갱신** — 성공한 마지막 message_id 로 갱신. **실패한 글의 ID 는 갱신하지 않는다** (다음 실행에서 재시도).
8. **완료 보고** — `N건 처리 (건너뜀 M건). 업데이트된 문서: (나열). 오늘 읽을 문서 추천: (가장 많이 추가된 테마 1개). 💭 기록을 잊지 마세요.`
   - 새 테마가 필요하면 `새 테마 제안: ○○ (이유)` 를 포함하고 사용자 승인을 받는다.
   - 월별 문서가 자동 생성됐으면 보고에 포함하고 위 매핑표를 갱신한다.
   - 근거 검증 실패로 건너뛴 글이 있으면 ID 와 사유를 보고한다.

---

## 7-1. 배치 이상 알림

배치가 정상 동작하지 않았을 때 사용자가 스케줄러 이력을 뒤지지 않고도 알 수 있어야 한다. 잡아야 하는 이상은 두 가지이고, 감지 주체가 다르다.

| 이상 유형 | 감지 주체 | 동작 |
|---|---|---|
| **배치가 돌았지만 실패** | `main.py` 의 batch 모드 | 예외를 잡아 `state.json` 에 `last_batch_result="fail"` 과 오류를 기록하고 알림 발송 후 예외를 재전파 (exit 1 유지) |
| **배치가 아예 돌지 않음** (스케줄러 고장, VM 꺼짐/로그아웃, `batch.lock` 잔존) | 워치독 `cli/check_batch_health.py` | `last_batch_at` 이 `BATCH_MAX_AGE_HOURS`(기본 26h)보다 오래되면 알림 발송 |

전달 경로는 두 개를 병행하며, 하나가 죽어도 다른 하나로 간다. **알림 실패가 배치를 중단시키지 않는다.**

1. **텔레그램 본인 저장 메시지** — 푸시라서 가장 빠르다 (`NOTIFY_TELEGRAM_CHAT_ID`, 기본 `me`)
2. **`99_생각노트` 문서 배너** — 매일 여는 문서라 놓치지 않는다

직접 확인할 때는 `python -m autonote.main --mode status` 를 쓴다. 종료 코드 0=정상, 2=이상.

워치독은 배치와 **독립적으로** 스케줄에 걸어야 의미가 있다. 등록:

```
schtasks /create /tn "autoNote_HealthCheck" /tr "D:\autoNote\cli\run_health_check.bat" /sc daily /st 09:40 /f
```

같은 이상을 하루에 여러 번 통보하지 않도록 `last_alert_date` 로 억제하고, 배치가 성공하면 억제를 해제한다. 실패 → 성공으로 바뀐 경우에만 복구 알림을 보낸다 (매일 성공 알림은 보내지 않는다).

### 스케줄 작업이 반드시 가져야 할 설정

| 설정 | 값 | 이유 |
|---|---|---|
| `StartWhenAvailable` (누락 시 가능한 한 빨리 시작) | **True** | VM 이 08:50 에 꺼져 있으면 그날 배치가 영영 안 돈다. 기본값은 False 다 |
| `MultipleInstances` | `IgnoreNew` | 중복 실행 방지. `batch.lock` 과 이중 방어 |

확인·설정:

```powershell
Get-ScheduledTask | Where-Object { $_.TaskName -match 'autoNote' } | ForEach-Object {
    "$($_.TaskName) : StartWhenAvailable=$($_.Settings.StartWhenAvailable)"
}
```

### 스케줄러 이력을 신뢰하려면

`schtasks` 의 `LastTaskResult` 는 **bat 이 종료 코드를 전파할 때만** 의미가 있다.
`echo` 가 `ERRORLEVEL` 을 0 으로 덮어쓰므로 `endlocal & exit /b %RC%` 로 끝내야 한다.

작업이 실제로 언제 돌았는지 사후 확인하려면 스케줄러 운영 로그가 켜져 있어야 한다
(기본 꺼짐). **관리자 권한** 으로 한 번 실행:

```powershell
wevtutil sl "Microsoft-Windows-TaskScheduler/Operational" /e:true
```

꺼져 있으면 시스템 부팅 로그(이벤트 12/13)로 역추적하는 수밖에 없다.

---

## 8. 테마 분류 규칙

- 테마는 **10개 이내** 유지. 잘게 쪼개지 않는다. (좋음: 반도체사이클 / 나쁨: HBM 을 별도 테마로)
- 테마는 `analyzer.THEMES` 의 enum 으로 강제된다. 새 테마는 코드·매핑표·스키마를 함께 갱신해야 한다.
- 한 글이 여러 테마에 걸치면 주된 테마 1개 + `관련 테마: ○○` 표기.

---

## 9. Docs 기록 및 서식 규칙

- 항상 문서 **맨 아래에 추가(append)**. 기존 내용 수정·삭제 금지. 특히 사용자가 채운 `▶ 💭` 는 절대 건드리지 않는다 (`내 생각 추가` 명령의 지정 항목 제외).
- 같은 message_id 를 두 번 기록하지 않는다.
- 문서를 못 찾으면 임의 생성하지 말고 보고한다. 기록 실패 시 블록을 터미널에 출력한다 (수동 붙여넣기용).
- **식별자 접두사는 `r` 로 통일한다**: `[r{메시지ID}]`. 원문보관·테마·종목·프레임 문서 모두 동일하게 쓴다 (`00_원문보관` 에 각인된 접두사와 일치시키기 위함).
- **`💭 내 생각` 미기록 줄에는 식별자를 붙인다**: `▶ 💭 내 생각: 미기록 [r755]`
  - Google Docs `replaceAllText` 는 **한 문단 안에서만** 일치하고 일치하는 **모든 곳**을 바꾼다. 식별자가 없으면 여러 블록의 `미기록` 줄이 구별되지 않아, 병합 시 무관한 블록을 오염시키거나 아무것도 바뀌지 않는다.
  - 식별자 없는 레거시 블록은 문서 내 `미기록` 줄이 유일할 때만 병합하고, 모호하면 병합을 거부하고 실패로 보고한다.
- **월별 원문보관 문서는 자동 생성된다**: 미등록 월 메시지가 오면 `00_원문보관 YYYY-MM` 을 만들어 세피아 테마를 적용하고 `state.json` 의 `original_docs` 에 등록한다. 생성 실패 시 다른 달 문서로 폴백하지 않고 중단한다.
- **서식**: 마크다운 기호(`#`, `##`, `**`)를 본문에 그대로 노출시키지 않는다. 문서 제목은 `heading1`+`bold`, 섹션 제목은 `heading2`+`bold`.
- **색상 통일**: 모든 문서는 세피아/라이트 배경(RGB `0.99, 0.96, 0.89` / `#FDF9E3`)과 다크 차콜 글자색(RGB `0.17, 0.21, 0.28`)을 유지한다. 검은 배경으로 생성되지 않아야 한다.

---

## 10. 주간리뷰 → `40_리뷰`

최근 7일 추가분 대상으로 다음 4개 항목을 작성한다. 근거는 항상 날짜로 인용한다.

1. **이번 주 핵심 논리 3가지** — 각각 "저자는 [근거]를 보고 [논리]로 [판단]했다" 한 문장 + 부연
2. **과거 기록 대비 저자 관점 변화** — "이전: → 현재:", 없으면 "변화 없음"
3. **사용자 💭 점검** — 저자와 일치/불일치 구분, 이번 주 글로 답할 수 있게 된 ❓ 표시. 💭 가 없는 항목이 많으면 기록을 부드럽게 독려
4. **다음 주 관찰 포인트 3개**

- **단일 통합 문서 `40_리뷰` 맨 아래에 누적(append)** 한다. 매주 새 문서를 만들지 않는다. 덮어쓰면 관점 변화 추적이라는 목적 자체가 무너진다.
- 생성 시점은 **주말이 지나고 그 주에 첫 배치가 기동하는 날**이다 (월요일 또는 휴가 후 첫 기동일).
- 생성 실패 시 주차를 기록하지 않아 다음 배치에서 재시도된다.

---

## 11. 절대 규칙

- **원문에 없는 사실을 추가하지 않는다.** 추론은 "판단 논리" 항목에서 원문 근거와 함께만.
  - LLM 경로가 실패하면 **아무것도 기록하지 않고 중단한다.** 규칙 기반으로 내용을 대체 생성하는 폴백을 두지 않는다. `state.json` 이 갱신되지 않으므로 다음 배치가 자동 재시도한다.
  - 근거(evidence)의 숫자는 프로그램이 원문과 대조 검증한다. 통과하지 못한 글은 기록하지 않는다.
- 저자의 전망을 확정적 사실처럼 쓰지 않는다.
- **종목 추천·매매 판단을 생성하지 않는다.** 이것은 학습 시스템이다.
- 사용자의 읽기·생각 기록을 대신하지 않는다. 💭 는 사용자의 몫이다.
- **Telethon 세션 파일과 API 자격증명을 출력·전송하지 않는다.**
  - Google Drive 동기화 포함: `ops/tools/sync_to_drive.py` 는 `.env` 계열, `telegram-mcp/`, 세션·키 파일을 제외한다(`is_excluded_file`). 이 제외 규칙을 완화하지 않는다.
  - 실값이 담긴 `.env` 는 커밋 금지. 키 목록은 `sample.env` 로만 관리한다.
- **배치 실행 원칙**: 에이전트가 자체 판단으로 Google Workspace 툴을 직접 호출해 문서를 임의로 작성하는 것을 금지한다. 반드시 `python -m autonote.main` 을 기동하는 방식으로 실행하고 `state.json` 일관성을 유지한다.
- 작업 세션 종료 시 `./docs/journal/YYYY-MM-DD_작업요약.md` 에 히스토리를 남긴다. 변경 내역, 미완료 작업, 다음 에이전트가 알아야 할 맥락을 포함할 것.
