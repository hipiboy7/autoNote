# 📒 autoNote — 투자 리서치 노트 자동화 시스템

텔레그램 채널의 투자 리서치 글을 자동으로 수집 → AI로 핵심 논리 분석 → Google Docs에 정밀하게 축적하는 하이브리드 자동화 시스템입니다.  
본 시스템은 **Google Antigravity (agy)** CLI 환경 위에서 동작합니다.

---

## 🏗️ 전체 구조

```
D:\autoNote\
├── AGENTS.md               ← 에이전트의 전반적인 동작 규칙
├── README.md               ← 지금 읽고 있는 종합 가이드 및 시스템 매뉴얼
├── data\
│   ├── state.json          ← 마지막으로 처리한 메시지 ID & 실행 시각 기록
│   └── extracted_messages.json
├── program\                ← ⭐ 신규 하이브리드 배치 프로그램 영역
│   ├── gemini.md           ← 본 프로젝트의 공식 협업 룰 & 가이드 (작업 규칙)
│   ├── pyproject.toml      ← uv workspace 패키지 관리
│   ├── src/                ← 하이브리드 제어 소스 코드
│   ├── tests/              ← TDD 유닛 테스트 코드
│   └── docs/               ← 요구사항, 설계서, 테스트결과서 산출물 위치
├── scripts\
│   ├── startup_agy.bat     ← VM 로그인 시 자동으로 agy를 기동하는 스크립트
│   ├── run_daily_batch.bat ← 매일 8:50 AM에 작업 스케줄러가 실행하는 배치 파일
│   ├── telegram_extractor.py
│   ├── docs_batch_uploader.py
│   ├── docs_formatter.py
│   ├── chunk_splitter.py
│   └── decrypt_token.js
├── telegram-mcp\           ← 텔레그램 MCP 서버 설정
└── working_docs\           ← 작업 히스토리 (에이전트 세션별 작업 요약 기록)
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
2. 프로그램이 각 구글 Docs(테마, 종목 등) 본문에서 고유 식별자(`[t305]` 등)를 검색하여, 해당 블록의 `▶ 💭 내 생각: 미기록` 부분을 **사용자의 코멘트로 자동 치환(replace)** 업데이트합니다. (대소문자 구분 없이 매칭)
3. 반영 완료된 항목은 생각노트에서 소거되거나 히스토리 보관함으로 자동 이동하여 중복 반영을 방지합니다.

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
$Shortcut.Arguments = '/k "D:\autoNote\scripts\startup_agy.bat"'
$Shortcut.WorkingDirectory = "D:\autoNote"
$Shortcut.Save()
```

### 배치 스케줄러 수동 시작 및 테스트
```powershell
# 8:50 배치작업 강제 즉시 실행 테스트
schtasks /run /tn "autoNote_DailyBatch"
```

---
*마지막 업데이트: 2026-07-15 (하이브리드 배치 및 생각노트 자동 병합 시스템 스펙 반영)*
