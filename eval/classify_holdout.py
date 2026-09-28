# -*- coding: utf-8 -*-
"""held-out 실패 4분류. results_holdout.jsonl 을 읽기만 한다 (코드 수정 없음).

분류 우선순위(1문항 1주라벨):
  retrieval    필수근거 recall < 1.0            → 근거를 못 가져왔다
  routing      라우팅/역질문/차단/전제교정/계산기 오판 → 실행 경로가 틀렸다
  generation   근거·경로는 맞는데 정답키 누락 / 금지어 위반
  verification 위 실패가 있는데 증명서가 전부 pass  → 검증이 못 잡았다 (교차 라벨)
"""
import json, collections, sys, os

BASE = os.path.expanduser("~/Desktop/프로젝트/미래에셋증권_AI페스티벌/pension-agent")
rows = [json.loads(l) for l in open(os.path.join(BASE, "eval/results_holdout.jsonl"), encoding="utf-8") if l.strip()]

stamp = collections.Counter((r.get("llm"), r.get("model")) for r in rows)
print("=" * 78)
print("LLM stamp:", dict(stamp), "| 문항", len(rows))
if any(k[0] != "hyperclova-x" for k in stamp):
    print("⚠️  실호출이 아닌 행이 있다 — 생성 지표 무효")
print("=" * 78)

def flags(r):
    g = r["채점"]
    f = []
    if g["근거recall"] is not None and g["근거recall"] < 1.0:
        f.append(("retrieval", f"근거recall {g['근거recall']:.2f}"))
    for k in ("라우팅", "역질문", "차단", "전제교정", "계산기"):
        if g[k] is False:
            f.append(("routing", k + "오판"))
    if g["정답키"] is False:
        f.append(("generation", "정답키누락"))
    if g["정답키_택1"] is False:
        f.append(("generation", "정답키택1누락"))
    if g["금지어위반"]:
        f.append(("generation", "금지어:" + ",".join(g["금지어위반"])))
    if (r.get("fallback") or {}).get("triggered"):
        f.append(("generation", "LLM폴백"))
    return f

cert_keys = ("L0_schema", "L1_policy", "L2_fact", "L3_intent")
PRIO = ["retrieval", "routing", "generation"]

buckets = collections.defaultdict(list)
silent = []   # 답 틀렸는데 verifier 통과 — 가장 위험
verif_caught = []

for r in rows:
    f = flags(r)
    g = r["채점"]
    cert = g["증명서"]
    all_pass = all(cert.get(k) for k in cert_keys)
    if not f:
        if not all_pass:
            buckets["verification"].append((r, [("verification", "실패없는데 증명서 fail")], cert))
        continue
    primary = next(p for p in PRIO if any(c == p for c, _ in f))
    buckets[primary].append((r, f, cert))
    # 생성/근거 오류인데 증명서가 전부 통과 = 침묵 실패
    substantive = [d for c, d in f if c in ("generation", "retrieval")]
    if substantive and all_pass:
        silent.append((r, f, cert))
    elif substantive and not all_pass:
        verif_caught.append((r, f, cert))

tot_fail = sum(len(v) for v in buckets.values())
print(f"\n## 실패 문항 {tot_fail} / {len(rows)}  (성공 {len(rows)-tot_fail})\n")
print("| 분류 | 문항수 |")
print("|---|---|")
for k in PRIO + ["verification"]:
    print(f"| {k} | {len(buckets[k])} |")

for k in PRIO + ["verification"]:
    if not buckets[k]:
        continue
    print(f"\n### {k} ({len(buckets[k])}건)\n")
    for r, f, cert in buckets[k]:
        cs = "".join("✓" if cert.get(x) else "✗" for x in cert_keys)
        print(f"- **{r['id']}** [{r['유형']}/{r['난이도']}] 기대→실제: {r['채점']}" if False else
              f"- **{r['id']}** [{r['유형']}→{r['라우팅실제']}] cert={cs} conf={r['채점']['confidence']}")
        print(f"    질의: {r['질의'][:70]}")
        print(f"    실패: {[d for _, d in f]}")
        if r.get("비고"):
            print(f"    비고: {r['비고']}")

print("\n" + "=" * 78)
print(f"## ★ 침묵 실패 — 답/근거 틀렸는데 증명서 전부 통과: {len(silent)}건")
print("=" * 78)
for r, f, cert in silent:
    print(f"\n- **{r['id']}** [{r['유형']}] conf={r['채점']['confidence']}")
    print(f"    질의: {r['질의']}")
    print(f"    실패: {[d for _, d in f]}")
    print(f"    비고: {r.get('비고','')}")
    print(f"    답변: {(r['answer'] or '')[:260]}")

print(f"\n## 검증이 잡아낸 실패: {len(verif_caught)}건 — " +
      ", ".join(r["id"] for r, _, _ in verif_caught))
