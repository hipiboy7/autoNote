"""Claude Code 세션 사용량을 API 정가 기준 달러로 환산해 누적 보고한다.

에이전트 작업 도구이며 autoNote 제품 코드가 아니다 (packages/ 밖에 두는 이유).

    python .claude/tools/claude_credit.py            # 기준일 이후 누적 + 스냅샷 기록
    python .claude/tools/claude_credit.py --refresh  # 기준을 지금으로 재설정 (사용자 요청 시에만)

* 출처: ~/.claude/projects/<프로젝트>/**/*.jsonl 의 assistant 메시지 usage (message.id 로 중복 제거)
* 기준·이력: var/claude_credit.json (gitignore 대상)
* 단가는 API 정가 환산값이다. 구독·조직 계약 요금과 다를 수 있다.
"""

import argparse
import glob
import json
import os
from datetime import datetime, timezone

PROJECT_DIR = os.path.expanduser(r"~/.claude/projects/D--autoNote")
LEDGER = os.path.join(os.path.dirname(__file__), "..", "..", "var", "claude_credit.json")
DEFAULT_SINCE = "2026-10-19T00:00:00+09:00"

# $/1M tokens: input, output, cache_read. cache write = input x1.25(5m) / x2(1h)
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-fable-5-1": (10.00, 50.00, 0.25),
}
FAST_MULTIPLIER = 2.0  # Opus 5.5 fast mode


def message_cost(model, usage):
    base = PRICES.get(model)
    if base is None:
        return None
    inp, out, read = base
    cc = usage.get("cache_creation") or {}
    w1h = int(cc.get("ephemeral_1h_input_tokens", 0) or 0)
    w5m = int(cc.get("ephemeral_5m_input_tokens", 0) or 0)
    if not cc:
        w5m = int(usage.get("cache_creation_input_tokens", 0) or 0)
    cost = (
        int(usage.get("input_tokens", 0) or 0) * inp
        + int(usage.get("output_tokens", 0) or 0) * out
        + int(usage.get("cache_read_input_tokens", 0) or 0) * read
        + w5m * inp * 1.25
        + w1h * inp * 2.0
    ) / 1_000_000
    if usage.get("speed") == "fast":
        cost *= FAST_MULTIPLIER
    return cost


def load_ledger():
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding="utf-8") as f:
            return json.load(f)
    return {"since": DEFAULT_SINCE, "snapshots": []}


def save_ledger(ledger):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=1)


def collect(since):
    seen = {}
    for path in glob.glob(os.path.join(PROJECT_DIR, "**", "*.jsonl"), recursive=True):
        session = os.path.basename(path).split(".")[0]
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = rec.get("message") or {}
                usage = msg.get("usage")
                ts = rec.get("timestamp")
                if rec.get("type") != "assistant" or not usage or not ts or not msg.get("id"):
                    continue
                if datetime.fromisoformat(ts.replace("Z", "+00:00")) < since:
                    continue
                seen[msg["id"]] = (session, msg.get("model", ""), usage)
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    ledger = load_ledger()
    now = datetime.now(timezone.utc)
    if args.refresh:
        ledger["snapshots"].append({"at": now.isoformat(), "event": "refresh",
                                    "previous_since": ledger["since"]})
        ledger["since"] = now.isoformat()
        save_ledger(ledger)
        print(f"refreshed: since={ledger['since']}")
        return

    since = datetime.fromisoformat(ledger["since"])
    total, unknown, sessions, tokens = 0.0, set(), {}, {"in": 0, "out": 0, "read": 0, "write": 0}
    for session, model, usage in collect(since).values():
        cost = message_cost(model, usage)
        if cost is None:
            unknown.add(model)
            continue
        total += cost
        sessions[session] = sessions.get(session, 0.0) + cost
        tokens["in"] += int(usage.get("input_tokens", 0) or 0)
        tokens["out"] += int(usage.get("output_tokens", 0) or 0)
        tokens["read"] += int(usage.get("cache_read_input_tokens", 0) or 0)
        tokens["write"] += int(usage.get("cache_creation_input_tokens", 0) or 0)

    prev = ledger["snapshots"][-1]["total_usd"] if ledger["snapshots"] and \
        "total_usd" in ledger["snapshots"][-1] else 0.0
    ledger["snapshots"].append({"at": now.isoformat(), "total_usd": round(total, 4),
                                "delta_usd": round(total - prev, 4)})
    save_ledger(ledger)

    print(f"since={ledger['since']} total=${total:.2f} (delta ${total - prev:.2f} since last report)")
    print(f"tokens in={tokens['in']:,} out={tokens['out']:,} "
          f"cache_read={tokens['read']:,} cache_write={tokens['write']:,}")
    for s, c in sorted(sessions.items(), key=lambda x: -x[1]):
        print(f"  session {s[:8]}: ${c:.2f}")
    if unknown:
        print(f"unpriced models skipped: {sorted(unknown)}")


if __name__ == "__main__":
    main()
