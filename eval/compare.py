# -*- coding: utf-8 -*-
"""dev vs held-out 일반화 오차 표 → eval/gap.md

held-out 은 규칙 튜닝에 쓰지 않은 세트다. 두 점수의 차이가 일반화 오차이며,
dev 점수만 보고하는 것은 자기 채점이다.
"""
import os, re, json, statistics
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EV = os.path.join(BASE, "eval")

def load(tag):
    p = os.path.join(EV, f"results_{tag}.jsonl")
    if not os.path.exists(p): return None
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]

def rate(rows, key):
    v = [r["채점"][key] for r in rows if r["채점"][key] is not None]
    return (sum(1 for x in v if x) / len(v) * 100) if v else None

def ev_rate(rows):
    v = [r["채점"]["근거recall"] for r in rows if r["채점"]["근거recall"] is not None]
    return statistics.mean(v) * 100 if v else None

dev, ho = load("dev"), load("holdout")
if not dev or not ho:
    raise SystemExit("먼저 두 세트를 모두 실행하세요:\n"
                     "  python3 eval/run_bench.py\n  python3 eval/run_bench.py --file holdout.jsonl")

METRICS = [("라우팅 정확도", lambda r: rate(r, "라우팅")),
           ("역질문 판정", lambda r: rate(r, "역질문")),
           ("차단 판정", lambda r: rate(r, "차단")),
           ("전제교정 판정", lambda r: rate(r, "전제교정")),
           ("필수근거 평균 recall", ev_rate),
           ("제어 종합점수", lambda r: statistics.mean(x["채점"]["제어점수"] for x in r) * 100)]

L = ["# 일반화 오차 — dev vs held-out", "",
     f"- dev {len(dev)}문항 (규칙 튜닝에 사용, in-sample) / held-out {len(ho)}문항 (튜닝 미사용)",
     "- **held-out 컬럼이 실제 성능 추정치다.** dev 는 회귀 탐지용이다.", "",
     "| 지표 | dev | held-out | 갭 |", "|---|---|---|---|"]
for name, fn in METRICS:
    a, b = fn(dev), fn(ho)
    if a is None or b is None: continue
    L.append(f"| {name} | {a:.1f}% | **{b:.1f}%** | {b-a:+.1f}pp |")

L += ["", "## held-out 유형별", "", "| 유형 | 문항 | 라우팅 | 제어점수 |", "|---|---|---|---|"]
for t in sorted({r["유형"] for r in ho}):
    sub = [r for r in ho if r["유형"] == t]
    rr = rate(sub, "라우팅")
    L.append(f"| {t} | {len(sub)} | {rr:.0f}% | {statistics.mean(x['채점']['제어점수'] for x in sub)*100:.1f}% |")

L += ["", "## 규칙", "",
      "1. held-out 실패를 보고 라우터·검색 규칙을 직접 고치지 않는다.",
      "2. 고칠 근거를 찾으면 **실패의 계열**을 파악해 dev 에 동형 문항을 추가하고 거기서 고친다.",
      "3. held-out 재측정은 그 뒤 1회만 한다. 반복 측정할수록 held-out 성질을 잃는다.",
      "4. 정답 라벨 오류 정정은 튜닝이 아니다. 단, 정정 사실을 문항 비고에 남긴다."]
open(os.path.join(EV, "gap.md"), "w", encoding="utf-8").write("\n".join(L))
print("\n".join(L))
