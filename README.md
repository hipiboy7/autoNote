# 📒 autoNote — 투자 리서치 노트 자동화 시스템

텔레그램 채널의 투자 리서치 글을 자동으로 수집 → AI로 핵심 논리 분석 → Google Docs에 정밀하게 축적하는 하이브리드 자동화 시스템입니다.  
본 시스템은 **Google Antigravity (agy)** CLI 환경 위에서 동작합니다.

---

## 🏗️ 전체 구조

```
D:\autoNote\
├── AGENTS.md               ← 운영 규칙 (에이전트가 먼저 읽는 문서)
├── antigravity.md          ← 문서 위치 안내 (규칙 정본은 docs/rules/개발규칙.md)
├── README.md               ← 지금 읽고 있는 시스템 매뉴얼
├── pyproject.toml          ← 워크스페이스 루트 (도구 설정 전용, 설치 대상 아님)
├── sample.env              ← 환경변수 목록 (cp sample.env .env)
│
├── packages/               ← 모든 파이썬 코드 (uv/pip workspace)
│   ├── app/                ← autonote 패키지 (배치·복구·알림·워치독)
│   ├── libs/               ← 공유 추상화 (agy-gateway, google-docs-client,
│   │                          telegram-client, llm-gateway)
│   ├── services/           ← 도메인 서비스 (collector, analyzer, writer, merger, reviewer)
│   └── tests/              ← 앱 계층 테스트 (패키지별 테스트는 각 패키지 안)
│
├── cli/                    ← 실행 진입점 (스케줄러가 가리키는 곳)
│   ├── run_daily_batch.bat     매일 08:50 스케줄러가 실행
│   ├── run_health_check.bat    매일 09:40 배치 이상 감시
│   ├── run_backfill.bat        구간 재처리
│   ├── startup_agy.bat         VM 로그인 시 agy 기동
│   └── check_batch_health.py   워치독 본체
│
├── ops/                    ← 운영 스크립트
│   ├── tools/              반복 사용 (sync_to_drive, verify_repair, decrypt_token)
│   ├── migrations/         일회성 보정 (relabel_dates_kst, dedupe_archive 등)
│   └── archive/            현 배선에서 미사용 (삭제 전 보관)
│
├── docs/                   ← 문서
│   ├── 00-신규프로젝트-설계교훈.md   ← 다른 프로젝트에도 쓰는 교훈 정리
│   ├── rules/개발규칙.md             ← 개발 그라운드 룰 정본
│   ├── phases/                      P01~P06 산출물
│   ├── journal/                     세션별 작업 기록
│   └── nextpjt/                     다음 프로젝트(다채널 수집) 설계 문서
│
├── var/                    ← 런타임 (git 제외, 통째로)
│   ├── state.json          마지막 처리 ID·배치 결과·문서 매핑
│   └── agy_usage.jsonl     워커 토큰 사용량 누적
│
├── vendor/                 ← 외부 클론 (git 제외)
│   └── telegram-mcp/       텔레그램 MCP 서버
│
└── .agents/skills/         ← agy 런북 (daily-batch, doc-repair, date-relabel)
```

> **경로 상수 주의.** `vendor/telegram-mcp` 와 `ops/tools/decrypt_token.js` 는 코드가
> 저장소 루트를 역산해 참조한다. 이 디렉토리를 옮기면 해당 상수와 테스트를 함께 고칠 것.
> 절대 경로를 박아 두었다가 2026-08-08~10 배치가 3일간 조용히 실패했다.

### 실행 방법

패키지가 editable 설치되어 있어 **`PYTHONPATH` 설정이 필요하지 않습니다.**

```powershell
python -m autonote.main --mode status     # 상태 판정 (0=정상, 2=이상)
python -m autonote.main --mode batch      # 일일 배치
D:\autoNote\cli\run_daily_batch.bat       # 스케줄러가 실행하는 것과 동일
```

설치가 풀렸다면 (새 환경·파이썬 재설치 후):

```powershell
cd D:\autoNote
foreach ($m in @("packages/libs/agy-gateway","packages/libs/google-docs-client","packages/libs/telegram-client","packages/libs/llm-gateway","packages/services/analyzer","packages/services/collector","packages/services/merger","packages/services/reviewer","packages/services/writer","packages/app")) { pip install -e $m --no-deps }
```

---

## 🔄 일상 워크플로우 및 사용 방법

### 1️⃣ 아침 배치 자동 실행 (매일 8:50 AM)
1. VM에 로그인되어 있다면 오전 8:50에 Windows 작업 스케줄러에 의해 배치 프로그램이 기동됩니다.
2. 텔레그램에서 어제 이후의 새 글들을 긁어와 분석한 후, 구글 Docs에 아래의 식별자를 각인하여 추가 기록합니다.
3. 배치가 끝날 때 에이전트는 사용자를 위해 `99_생각노트` 구글 문서에 **당일 글의 요약과 본문 Docs 링크가 포함된 템플릿(Pre-fill)**을 자동으로 차려둡니다.

### 2️⃣ 내 생각 적기
1. 사용자는 여러 Docs 문서를 헤맬 필요 없이, 오직 하나의 구글 문서인 **`99_생각노트`**만 엽니다.
2. 에이전트가 미리 차려놓은 템플릿의 `💭 내 생각: ` 우측 영역에 자유롭게 본인의 의견, 아이디어, 추가 공부할 사항을 한 줄씩 적습니다.
   * *예시:*
     ```markdown
     * [t305] 🔗[반도체사이클 바로가기] | 삼성전자 ETF 상품 리스크 분석
       └ 💭 내 생각: 레버리지 ETF의 복리 깎임 현상이 생각보다 크네. 장기 보유 금지.
     ```

### 3️⃣ 자동 생각 병합 (다음 날 아침)
1. 다음 날 아침 배치가 실행될 때, 프로그램은 가장 먼저 `99_생각노트`에 사용자가 입력해 둔 생각 코멘트를 읽어 들입니다.
2. 프로그램이 각 구글 Docs(테마, 종목 등) 본문에서 고유 식별자(`[t305]` 등)를 검색하여, 해당 블록의 `▶ 💭 내 생각: 미기록 [r305]` 줄을 **사용자의 코멘트로 자동 치환(replace)** 업데이트합니다. (대소문자 구분 없이 매칭)
   * 미기록 줄에 식별자가 붙어 있는 이유: Google Docs 치환 API 는 한 문단 안에서만 일치하고 일치하는 모든 곳을 바꾸므로, 식별자 없이는 여러 블록의 `미기록` 줄을 구별할 수 없습니다.
3. 반영 완료된 항목만 생각노트에서 `[r305_처리완료]` 로 표시됩니다.
   * **치환이 실제로 반영되었는지 읽기 검증 후에만** 처리완료로 표시합니다. 실패한 항목은 생각노트에 그대로 남고 터미널에 보고되므로, 적어둔 생각이 유실되지 않습니다.

---

## 🔍 문서 고유 식별자(ID) 규칙

찾기(Ctrl+F)로 관련 본문을 빠르게 추적하고 생각노트와 정확하게 연동하기 위해, 각 문서에는 고유한 접두사를 붙인 메시지 식별자가 부여됩니다. 자릿수가 늘어나더라도(예: `1024` 등) 뒤에 그대로 이어서 붙여 나갑니다.

*   **원본 보관 문서:** `[r{메시지ID}]` (예: `[r305]`, `[r1024]`)
*   **테마 노트 문서:** `[t{메시지ID}]` (예: `[t305]`, `[t1024]`)
*   **종목 노트 문서:** `[s{메시지ID}]` (예: `[s305]`, `[s1024]`)
*   **프레임 문서:** `[f{메시지ID}]` (예: `[f305]`, `[f1024]`)

---

## 📄 Google Docs 문서 체계

| 문서 분류 | 파일 이름 및 링크 | 용도 |
|-----------|------------------|------|
| **생각 입력창구** | `99_생각노트` | 사용자가 💭 내 생각을 적는 단일 진입로 |
| 원문 보관 | [00_원문보관 2026-07](https://docs.google.com/document/d/17b8iLt_KxjWZX7On9hnPXCLZtm6fpT8GBgYb_X85PHY/edit) | 텔레그램 원문 그대로 수정 없이 보관 |
| 테마: 반도체사이클 | [10_테마 반도체사이클](https://docs.google.com/document/d/1UeI5DBboBk-Tedvu4G6ucOuZdxJAtkRg55N_VMV4U-s/edit) | 반도체 업황, 사이클 분석 지식 축적 |
| 테마: 시장심리·수급 | [10_테마 시장심리·수급](https://docs.google.com/document/d/1k2vvUvzgl6iW08MGbY9oehc2zBnKtPAi-z6OqrwaSoA/edit) | 시장 수급, 투자 심리, 매크로 센티먼트 |
| 테마: 환율·매크로 | [10_테마 환율·매크로](https://docs.google.com/document/d/18lU0RmfkofcRuosdPn664x4AG8DSO12gvsDqLUi2eKE/edit) | 환율 동향, 거시경제 데이터 분석 |
| 테마: 금리 | [10_테마 금리](https://docs.google.com/document/d/1PK88LaNGhBrxGC0xFGX4tkTPUU15kqzuOhK5SzQ74Eo/edit) | 연준 금리 정책 및 통화 긴축/완화 기조 |
| 테마: 방산·방위산업 | [10_테마 방산·방위산업](https://docs.google.com/document/d/1AvDpijnMrsi92m5OGg2APcg8xfniGA-jiXkiA6ygIJQ/edit) | 지정학 리스크 및 글로벌 방위산업 동향 |
| 종목: 한화에어로 | [20_종목 한화에어로스페이스](https://docs.google.com/document/d/1F2YJCAtqznASlhY9351UA0qX-81bw2hbrqQM6qrrd9I/edit) | 한화에어로스페이스 기업 개별 코멘트 |
| 종목: 에이유브랜즈 | [20_종목 에이유브랜즈](https://docs.google.com/document/d/1cUfOHw0OAV0Wwpi5qM4aR3BSi2-H76aV7F8L9RtK-ek/edit) | 에이유브랜즈 기업 개별 코멘트 |
| 종목: 에이피알 | [20_종목 에이피알](https://docs.google.com/document/d/1zC3oDw1kbP651QjsPHztgzo7aCrZr7qPczddgdwmEvY/edit) | 에이피알 기업 개별 코멘트 |
| 판단 프레임 | [30_프레임 판단프레임모음](https://docs.google.com/document/d/1wxRABv-jRTSsqpC4-EC2Ty9VUctZ9SBWr_FMIHEQ8sE/edit) | 저자가 반복해 사용하는 판단의 뼈대 |
| 리뷰 | `40_리뷰 YYYY-Wxx` | 매주 토요일 배치 시 자동 생성되는 주간 분석 |

---

## 🤖 AI 모델 2-tier 구성
*   **🧠 두뇌 (핵심 논리 추출 + 테마 분류 + 주간 리뷰 생성):** `Claude Opus 4.6 (Thinking)` (백업 1차: `Gemini 3.5 Flash (High)`, 백업 2차: `Gemini 3.1 Pro (High)`)
*   **⚡ 실행 (프로그램 제어 및 Docs 기록 관리):** `Gemini 3.5 Flash (Medium)` (백업: `Gemini 3.1 Pro (Low)`)

---

## 🔧 문제 해결 및 트러블슈팅

### VM 로그인 시 agy 기동 우회/복구
```powershell
# 바로가기가 정상 작동하는지 확인
dir "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\autoNote_agy.lnk"

# 끊어졌다면 바로가기 복구 스크립트 실행
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\autoNote_agy.lnk")
$Shortcut.TargetPath = "cmd.exe"
$Shortcut.Arguments = '/k "D:\autoNote\cli\startup_agy.bat"'
$Shortcut.WorkingDirectory = "D:\autoNote"
$Shortcut.Save()
```

### 배치 스케줄러 수동 시작 및 테스트
```powershell
# 8:50 배치작업 강제 즉시 실행 테스트
schtasks /run /tn "autoNote_DailyBatch"
```

---
*마지막 업데이트: 2026-08-07 (디렉토리 구조 정리, editable 설치로 PYTHONPATH 제거)*
