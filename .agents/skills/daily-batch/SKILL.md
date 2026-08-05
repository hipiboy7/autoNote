---
name: daily-batch
description: >-
  autoNote 일일 배치를 기동하고 결과를 해석해 보고한다. 사용자가 '일일 배치',
  '업데이트', '배치 돌려줘', '수집해줘', '상태' 라고 하거나, 배치 실패 로그를
  분석해야 할 때 사용한다.
---

# 일일 배치 운영 런북

## 원칙

**에이전트가 Google Workspace 툴을 직접 호출해 문서를 작성하지 않는다.** 반드시 아래 프로그램을 기동하는 방식으로 실행한다. 상태 파일(`state.json`) 일관성이 이 규칙에 달려 있다.

## 실행

```powershell
D:\autoNote\scripts\run_daily_batch.bat
```

개별 모드가 필요하면 `PYTHONPATH` 를 잡고 직접 호출한다. **`program/libs/agy-gateway/src` 가 빠지면 import 오류로 죽는다.**

```powershell
cd D:\autoNote
$env:PYTHONPATH="program/src;program/libs/agy-gateway/src;program/libs/google-docs-client/src;program/libs/telegram-client/src;program/libs/llm-gateway/src;program/services/analyzer/src;program/services/collector/src;program/services/merger/src;program/services/reviewer/src;program/services/writer/src"
$env:PYTHONIOENCODING="utf-8"
python program/src/main.py --mode batch     # 또는 status / review / merge / backfill
```

## 파이프라인 단계와 정상 로그

```
[BATCH] 배치 잠금 획득 완료 (PID: ...)
[Config] 워커 로스터: triage=... extract=... review=... repair=...
[Batch] Step 1: Merging yesterday's thoughts...
[Batch] Step 2: Collecting new messages...
Collection statistics for -1004421517798: Collected=N new messages.
[Batch] Step 3: Triaging N messages...
[Batch] Triage: research=X, skipped=Y (본문 없음 Z건)
[Batch] Step 4: Extracting logic from X research posts...
[Batch] Pipeline completed successfully. Processed=..., Skipped=...
```

LLM 호출은 Step 3(판별)과 Step 4(추출)에서 **배치로만** 일어난다. 호출 1건당 input 약 20.7k 토큰이 고정 오버헤드이므로 건별 호출은 낭비다. 로그에 호출이 메시지 수만큼 찍히면 배치가 깨진 것이니 조사할 것.

## 실패 유형별 대응

| 로그 | 원인 | 대응 |
|---|---|---|
| `근거 검증 실패: 원문에 없는 숫자 [...]` | 워커가 원문에 없는 수치를 근거로 씀 | **정상 동작이다.** 창작을 막은 것. 해당 글은 기록되지 않고 `state.json` 도 그 앞에서 멈춘다. 재실행하면 재시도된다. 같은 글이 반복 실패하면 원문을 직접 읽고 판단해 보고할 것 |
| `워커 실행이 N회 모두 실패` | agy 호출 실패 | `agy models` 로 모델명 확인. 로스터의 모델이 목록에 있어야 한다 |
| `테마 ... 문서 매핑이 없습니다` | `analyzer.THEMES` 와 `doc_mappings` 불일치 | 둘을 일치시킨다. 조용한 폴백은 두지 않는다 |
| `문서 매핑이 없는 테마: [...]` (기동 즉시) | 위와 동일, 기동 시 검증에서 잡힘 | |
| `다른 배치 프로세스가 이미 실행 중입니다` | 잠금 충돌 | 중복 실행 방지 장치. 기다린다 |
| `replace_line 검증 실패` | Docs 치환이 반영되지 않음 | 생각노트에 의견이 남아 있으므로 유실 아님. 다음 배치에서 재시도 |
| `[CommentMerger] 병합 실패 N건` | 식별자 특정 불가 | 레거시 블록(식별자 없는 `미기록`)이 여럿인 경우. 출력된 블록을 수동 반영하거나 문서 복구를 검토 |

**중요: 실패 시 `state.json` 을 손으로 고치지 않는다.** 실패한 글의 ID 를 건너뛰면 그 글이 영구 누락된다.

## 완료 보고 형식

```
N건 처리 (건너뜀 M건). 업데이트된 문서: (나열).
오늘 읽을 문서 추천: (가장 많이 추가된 테마 1개).
💭 기록을 잊지 마세요.
```

추가로 포함할 것:
- 근거 검증 실패로 건너뛴 글이 있으면 ID 와 사유
- 월별 원문보관 문서가 자동 생성됐으면 문서명·ID (그리고 AGENTS.md 매핑표 갱신)
- 새 테마가 필요하면 `새 테마 제안: ○○ (이유)` — 사용자 승인 전에는 문서를 만들지 않는다
- 토큰 사용량은 `data/agy_usage.jsonl` 에 누적된다. 이상 급증이 보이면 배치 크기를 점검

## 세션 종료

`./working_docs/YYYY-MM-DD_작업요약.md` 에 변경 내역·미완료 작업·다음 에이전트가 알아야 할 맥락을 남긴다.
