---
name: date-relabel
description: >-
  과거 기록의 날짜 라벨을 UTC 기준에서 KST 기준으로 바로잡는다. 사용자가 '날짜 보정',
  '날짜 라벨 고쳐줘', '문서 날짜가 하루 밀렸다' 라고 할 때 사용한다. 일회성 작업이다.
---

# 날짜 라벨 KST 보정 런북

## 무엇을 왜 고치는가

`writer` 가 메시지 시각(UTC)을 그대로 `%Y-%m-%d` 로 찍었다. KST = UTC+9 이므로
**한국 시간 오전 0~9시에 올라온 글이 전날로 기록**됐다.

```
r806  UTC 08-05 23:17  =  KST 08-06 08:17  →  문서에 '2026-08-05'
r811  UTC 08-05 23:51  =  KST 08-06 08:51  →  문서에 '2026-08-05'
```

기록 누락은 없다. 라벨만 하루 밀렸다. **코드는 이미 고쳤으므로(`writer/timebase.py`)
새 기록은 정확하다.** 이 런북은 이미 찍힌 과거 라벨만 보정한다.

2026-08-06 dry-run 기준 대상: **775건** (시각 미확보 261건은 조회 범위 확대 필요).

## 실행

```powershell
cd D:\autoNote
$env:PYTHONIOENCODING="utf-8"

# 1) 반드시 먼저 집계만 (문서를 바꾸지 않는다)
python ops\migrations\relabel_dates_kst.py --dry-run --limit 1000

# 2) 작은 문서부터 하나씩 확인하며 진행
python ops\migrations\relabel_dates_kst.py 에이피알 --limit 1000
python ops\migrations\relabel_dates_kst.py 한화에어로스페이스 --limit 1000
python ops\migrations\relabel_dates_kst.py 방산·방위산업 --limit 1000
python ops\migrations\relabel_dates_kst.py 금리 --limit 1000

# 3) 큰 문서
python ops\migrations\relabel_dates_kst.py 환율·매크로 --limit 1000
python ops\migrations\relabel_dates_kst.py "시장심리·수급" --limit 1000
python ops\migrations\relabel_dates_kst.py "원문보관 2026-07" --limit 1000
python ops\migrations\relabel_dates_kst.py "원문보관 2026-08" --limit 1000
python ops\migrations\relabel_dates_kst.py 반도체사이클 --limit 1000
```

**한 문서씩 실행하고 매번 결과를 확인할 것.** 스크립트는 문서 본문을 통째로 재작성하므로
(건당 치환은 775건에 비현실적으로 느리다) 실패 시 그 문서만 영향받게 격리하는 편이 좋다.

## 결과 읽는 법

정상 출력:
```
[Relabel] 금리: 라벨 불일치 12건 / 시각 미확보 0건
[Relabel]   [r518] 2026-07-26 → 2026-07-27
[SepiaTheme] 세피아/라이트 테마 적용 완료: ...
[Relabel] 금리: 12/12건 보정 완료 (OK)
```

- **`(OK)`** — 재작성 후 남은 불일치 0. 성공.
- **`(잔존 N건)`** — 실패로 계산된다. 해당 문서를 열어 확인하고 사용자에게 보고할 것.
- **`시각 미확보 N건`** — 텔레그램 조회 범위(`--limit`) 밖의 오래된 글이다. 라벨을
  판정할 수 없어 **건드리지 않는다**. `--limit` 을 늘려 다시 실행하면 줄어든다.
  그래도 남으면 채널에서 이미 삭제된 메시지일 수 있으므로 사용자에게 보고한다.
- 종료 코드: 0 성공 / 1 일부 실패.

## 주의

- **`--dry-run` 없이 전체(`인자 없음`)를 한 번에 돌리지 말 것.** 10개 문서를 연속
  재작성하므로 중간 실패 시 어디까지 됐는지 파악이 번거롭다.
- 08:50 일일 배치와 겹치지 않게 실행할 것. 같은 `batch.lock` 을 쓰지 않으므로 충돌
  감지가 되지 않는다. 배치 중 문서를 재작성하면 그 배치의 기록이 유실될 수 있다.
- 문서 본문을 손으로 고치지 말 것. 과거에 수동 편집으로 인접 메시지의 숫자가 섞여
  들어간 사례가 있다 (시장심리·수급 r753 의 '640p' 는 r755 의 숫자였다).

## 완료 후

1. 보정 건수와 잔존·미확보 건수를 사용자에게 보고한다.
2. `python ops\tools\verify_repair.py` 는 `doc_overrides` 기반이라 이 작업과는 무관하다.
   대신 `--dry-run` 을 다시 돌려 대상이 0건이 되었는지 확인한다.
3. `working_docs/YYYY-MM-DD_작업요약.md` 에 보정 이력을 남긴다.
