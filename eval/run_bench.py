# -*- coding: utf-8 -*-
"""벤치마크 채점 harness.

지표를 두 층으로 나눈다.
 · 제어 지표 — 라우팅·역질문·차단·전제교정·계산기·근거 recall. LLM 없이(Mock) 도 측정된다.
 · 생성 지표 — 정답키 포함률·금지어·L2 팩트. HyperCLOVA X 연결 후에야 의미가 있다.
Mock 상태에서 생성 지표가 낮은 것은 정상이며, 리포트에 그 사실을 명시한다.

사용: python3 eval/run_bench.py [--llm real|mock|auto] [--file holdout.jsonl] [--limit N]

⚠️ Mock 침묵 폴백 금지 — 이 하네스는 .env 를 직접 로드한다.
   과거 이 파일이 .env 를 읽지 않아 CLOVA_API_KEY 가 비었고, get_llm() 이 조용히 MockLLM 으로
   떨어져 dev 100건 중 89건이 [MOCK] 응답으로 채점됐다. 제어 지표는 LLM 이전 계층이라 유효했지만
   생성 지표는 전부 무의미했다. 같은 사고를 막기 위해:
     ① .env 를 로드한다  ② --llm real 이면 스모크 호출로 실제 연결을 확인하고 실패 시 중단한다
     ③ 사용한 LLM 이름·모델을 결과 행마다 stamp 한다 (사후에 mock/real 구분이 가능해야 한다)
"""
import os, sys, json, time, argparse, statistics
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))

# ── .env 로드 (반드시 pipeline import 보다 먼저) ──────────────────────────
_envp = os.path.join(BASE, ".env")
if os.path.exists(_envp):
    for _l in open(_envp, encoding="utf-8"):
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            _k, _v = _l.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass

from pipeline import Agent   # noqa: E402


def norm(s: str) -> str:
    return "".join((s or "").split()).replace(",", "").lower()


def grade(it, res, tr):
    exp = it["기대동작"]
    ans = res["answer"]
    ansn = norm(ans)
    doc_ids = (tr.get("retrieval") or {}).get("doc_ids") or []
    calls = [c.get("fn") for c in (tr.get("compute") or [])]
    clarify = (tr.get("slots") or {}).get("action") == "clarify"
    g = {
        "라우팅": (None if exp["차단"] else tr.get("route", {}).get("type") == exp["라우팅"]),
        "역질문": clarify == exp["역질문"],
        "차단": (tr.get("stop") if tr.get("stop") in ("injection", "personal_data") else None) == exp["차단"],
        "전제교정": bool((tr.get("premise") or {}).get("detected")) == exp["전제교정"],
        "계산기": set(exp["계산기"]).issubset(set(calls)) if exp["계산기"] else None,
        # 필수근거 항목은 "doc46|doc20" 처럼 any-of 그룹을 쓸 수 있다.
        # 같은 사안을 다루는 문서가 여럿일 때 그중 하나만 특정해 채점하면 검색 품질이 아니라
        # 스펙 작성자의 취향을 재는 지표가 된다.
        "근거recall": (sum(1 for grp in it["필수근거"]
                         if set(grp.split("|")) & {x.split("#")[0] for x in doc_ids}) / len(it["필수근거"]))
                     if it["필수근거"] else None,
        # 정답키 항목도 "A|B" any-of 를 허용한다(필수근거와 같은 규약).
        # 같은 사실을 여러 표현으로 쓸 수 있을 때 표기 하나를 강요하면
        # 정확성이 아니라 문장 스타일을 재게 된다.
        "정답키": (all(any(norm(a) in ansn for a in k.split("|")) for k in it["정답키"])
                 if it["정답키"] else None),
        "정답키_택1": (any(norm(k) in ansn for k in it["정답키_택1"]) if it["정답키_택1"] else None),
        "금지어위반": [w for w in it["금지"] if norm(w) in ansn],
    }
    cert = tr.get("certificate") or {}
    g["증명서"] = {k: (cert.get(k) or {}).get("pass") for k in ("L0_schema", "L1_policy", "L2_fact", "L3_intent")}
    g["confidence"] = tr.get("confidence")
    g["latency_ms"] = tr.get("latency_ms")
    ctrl = [x for x in (g["라우팅"], g["역질문"], g["차단"], g["전제교정"]) if x is not None] + \
           ([g["계산기"]] if g["계산기"] is not None else []) + \
           ([g["근거recall"] == 1.0] if g["근거recall"] is not None else [])
    g["제어점수"] = round(sum(1 for x in ctrl if x) / len(ctrl), 3)
    return g


def rate(vals):
    v = [x for x in vals if x is not None]
    return (sum(1 for x in v if x) / len(v), len(v)) if v else (None, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--type", default=None)
    ap.add_argument("--out", default=os.path.join(BASE, "eval"))
    ap.add_argument("--file", default="benchmark.jsonl", help="benchmark.jsonl(dev) 또는 holdout.jsonl")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--llm", choices=["auto", "real", "mock"], default="auto",
                    help="real = 실호출 강제(연결 실패 시 중단) / mock = 제어 지표만 / auto = 키 있으면 실호출")
    a = ap.parse_args()

    if a.llm == "mock":
        os.environ.pop("CLOVA_API_KEY", None)
    elif a.llm == "real" and not os.getenv("CLOVA_API_KEY"):
        sys.exit("[중단] --llm real 인데 CLOVA_API_KEY 가 없다. .env 를 확인해라.")

    tag = a.tag or ("holdout" if "holdout" in a.file else "dev")
    items = [json.loads(l) for l in open(os.path.join(BASE, "eval", a.file), encoding="utf-8") if l.strip()]
    if a.type:  items = [x for x in items if x["유형"] == a.type]
    if a.limit: items = items[:a.limit]
    assert len({x["id"] for x in items}) == len(items), "벤치마크 id 중복 — 캐시 오염으로 채점이 무효가 된다"

    t0 = time.time(); agent = Agent()
    llm_name = agent.llm.name
    model = os.getenv("CLOVA_MODEL", "-") if llm_name != "mock" else "-"

    # 실호출 모드면 스모크 호출로 연결을 먼저 확인한다. 여기서 죽는 편이
    # 80문항을 폴백 응답으로 채우고 그걸 성능이라고 보고하는 것보다 낫다.
    if a.llm == "real":
        if llm_name == "mock":
            sys.exit("[중단] --llm real 인데 MockLLM 이 선택됐다. get_llm() 과 .env 를 확인해라.")
        try:
            probe = agent.llm.chat([{"role": "user", "content": "'ok' 라고만 답하세요."}],
                                   temperature=0.0, max_tokens=8)
        except Exception as e:
            sys.exit(f"[중단] 실호출 스모크 실패: {type(e).__name__}: {str(e)[:200]}")
        print(f"스모크 OK → {probe.strip()[:40]!r}")
    if a.llm == "auto" and llm_name == "mock":
        print("⚠️  MockLLM 으로 실행한다. 생성 지표(정답키·L2)는 무의미하니 제어 지표만 읽어라.")

    print(f"agent ready ({time.time()-t0:.1f}s) | llm={llm_name} | model={model} | 문항 {len(items)}")

    rows = []
    for i, it in enumerate(items, 1):
        res = agent.answer(it["id"], it["질의"])
        # 상한이 걸리면 think_trace 는 축약본이다. 제어 채점은 축약 전 전체 증명서로 한다.
        tr = agent.last_trace or json.loads(res["think_trace"])
        g = grade(it, res, tr)
        g["len_think"] = len(res["think_trace"])
        g["len_context"] = len(res["retrieved_context"])
        g["len_answer"] = len(res["answer"])
        g["ctx_structured"] = (tr.get("retrieval") or {}).get("structured", 0)
        rows.append({"id": it["id"], "llm": llm_name, "model": model,
                     "유형": it["유형"], "난이도": it["난이도"], "질의": it["질의"],
                     "채점": g, "라우팅실제": tr.get("route", {}).get("type"),
                     "stop": tr.get("stop"), "doc_ids": (tr.get("retrieval") or {}).get("doc_ids"),
                     "compute": [c.get("fn") for c in (tr.get("compute") or [])],
                     "fallback": tr.get("fallback"),
                     "answer": res["answer"], "비고": it["비고"],
                     # 검증 로직을 바꾼 뒤 LLM 재호출 없이 오프라인 재채점하려면
                     # 답변 전문·근거 원문·증명서가 그대로 남아 있어야 한다.
                     # 잘라 두면 검증을 손볼 때마다 실호출 비용이 다시 든다.
                     "retrieved_context": res["retrieved_context"],
                     "certificate": tr.get("certificate"),
                     "confidence": tr.get("confidence")})
        print(f"  [{i:>2}/{len(items)}] {it['id']} {it['유형']:<6} 제어 {g['제어점수']:.2f} "
              f"{'' if g['라우팅'] else '라우팅✗ '}{'' if g['역질문'] else '역질문✗ '}"
              f"{'' if g['차단'] else '차단✗ '}{'' if g['전제교정'] else '전제✗ '}"
              f"{'' if g['계산기'] in (True, None) else '계산기✗ '}"
              f"{'' if g['근거recall'] in (1.0, None) else '근거%.1f ' % (g['근거recall'] or 0)}")

    with open(os.path.join(a.out, f"results_{tag}.jsonl"), "w", encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")

    G = [r["채점"] for r in rows]
    lat = sorted(g["latency_ms"] for g in G if g["latency_ms"] is not None)
    p = lambda q: lat[min(int(len(lat)*q), len(lat)-1)] if lat else 0
    ev_full = [g["근거recall"] for g in G if g["근거recall"] is not None]

    fb = sum(1 for r in rows if (r.get("fallback") or {}).get("triggered"))
    L = [f"# 벤치마크 채점 리포트 — {tag}", "",
         (f"- 모델: **{llm_name}** (`{model}`)" if llm_name != "mock"
          else "- 모델: **mock** ⚠️ 실호출 아님 — 생성 지표는 측정된 것이 아니다. 제어 지표만 읽어라."),
         f"- LLM 생성 실패 폴백: {fb}건" + (" ✅" if fb == 0 else " ⚠️ 해당 문항의 생성 지표는 무효"),
         f"- 문항: {len(rows)}개 · 실행: {time.strftime('%Y-%m-%d %H:%M')}",
         f"- 지연: p50 {p(0.5)}ms / p95 {p(0.95)}ms / max {lat[-1] if lat else 0}ms", "",
         ("> ⚠️ dev 세트다. 라우터·가드레일 규칙을 손볼 때 함께 참조했으므로 in-sample 이고,\n"
          "> 여기서의 제어 점수는 **회귀 탐지용**이다. 일반화는 holdout 리포트를 봐야 한다."
          if tag == "dev" else
          "> ✅ held-out 세트다. 규칙 튜닝에 사용하지 않았다.\n"
          "> dev 점수와의 차이가 **일반화 오차**다. 이 세트를 보고 규칙을 고치면 그 순간 held-out 이 아니게 된다."), "",
         "## 제어 지표 (LLM 무관)", ""]
    for name, vals in [("라우팅 정확도", [g["라우팅"] for g in G]),
                       ("역질문 판정 정확도", [g["역질문"] for g in G]),
                       ("차단 판정 정확도", [g["차단"] for g in G]),
                       ("전제교정 판정 정확도", [g["전제교정"] for g in G]),
                       ("계산기 호출 정확도", [g["계산기"] for g in G]),
                       ("필수근거 완전회수", [g["근거recall"] == 1.0 for g in G if g["근거recall"] is not None])]:
        r, n = rate(vals)
        L.append(f"- {name}: **{r*100:.1f}%** ({n}문항)" if r is not None else f"- {name}: 해당 없음")
    if ev_full: L.append(f"- 필수근거 평균 recall: **{statistics.mean(ev_full)*100:.1f}%**")
    L.append(f"- 제어 종합점수 평균: **{statistics.mean(g['제어점수'] for g in G)*100:.1f}%**")

    L += ["", "## 생성 지표 (HyperCLOVA X 연결 후 유효)", ""]
    for name, vals in [("정답키 전량 포함", [g["정답키"] for g in G]),
                       ("정답키(택1) 포함", [g["정답키_택1"] for g in G])]:
        r, n = rate(vals)
        L.append(f"- {name}: **{r*100:.1f}%** ({n}문항)" if r is not None else f"- {name}: 해당 없음")
    bad = [r for r in rows if r["채점"]["금지어위반"]]
    L.append(f"- 금지어 위반: **{len(bad)}건**" + (" ✅" if not bad else " → " + ", ".join(x["id"] for x in bad)))

    L += ["", "## 검증 증명서 통과율", ""]
    for lv in ("L0_schema", "L1_policy", "L2_fact", "L3_intent"):
        r, n = rate([g["증명서"][lv] for g in G])
        L.append(f"- {lv}: **{r*100:.1f}%**" if r is not None else f"- {lv}: -")
    L.append(f"- 평균 confidence: **{statistics.mean(g['confidence'] for g in G if g['confidence'] is not None):.2f}**")

    L += ["", "## 유형별 제어 점수", "", "| 유형 | 문항 | 제어점수 | 라우팅 |", "|---|---|---|---|"]
    for t in sorted({r["유형"] for r in rows}):
        sub = [r for r in rows if r["유형"] == t]
        rr, _ = rate([r["채점"]["라우팅"] for r in sub])
        L.append(f"| {t} | {len(sub)} | {statistics.mean(r['채점']['제어점수'] for r in sub)*100:.1f}% | {rr*100:.0f}% |")

    # ── 응답 필드 길이 분포 (주최측 상한 미고지 상태의 안전마진 산정 근거) ──
    def q(v, x):
        v = sorted(v)
        return v[min(int(len(v)*x), len(v)-1)] if v else 0
    L += ["", "## 응답 필드 길이 분포 (문자 수)", "",
          "> 주최측이 `think_trace`·`retrieved_context` 길이 상한을 아직 고지하지 않았다.",
          "> 아래 p95 를 기준으로 `MAX_THINK_TRACE` 안전마진을 잡는다. 상한이 통보되면 그 값만 바꾸면 된다.",
          "", "| 필드 | 중앙값 | p95 | 최대 |", "|---|---|---|---|"]
    for label, key in [("think_trace", "len_think"), ("retrieved_context", "len_context"), ("answer", "len_answer")]:
        v = [g[key] for g in G]
        L.append(f"| {label} | {q(v,0.5):,} | {q(v,0.95):,} | {max(v):,} |")
    L += ["", "| 유형 | 문항 | think_trace p95 | retrieved_context p95 | 최대 context |", "|---|---|---|---|---|"]
    for t in sorted({r["유형"] for r in rows}):
        sub = [r["채점"] for r in rows if r["유형"] == t]
        L.append(f"| {t} | {len(sub)} | {q([g['len_think'] for g in sub],0.95):,} | "
                 f"{q([g['len_context'] for g in sub],0.95):,} | {max(g['len_context'] for g in sub):,} |")
    st = [g for g in G if g["ctx_structured"]]
    if st:
        L.append("")
        L.append(f"- 정형 근거 사용 문항 {len(st)}건: retrieved_context 중앙값 "
                 f"{q([g['len_context'] for g in st],0.5):,}자 / p95 {q([g['len_context'] for g in st],0.95):,}자")

    fails = [r for r in rows if r["채점"]["제어점수"] < 1.0]
    L += ["", f"## 제어 실패 문항 {len(fails)}건", ""]
    for r in fails:
        why = [k for k in ("라우팅", "역질문", "차단", "전제교정")
               if r["채점"][k] is not None and not r["채점"][k]]
        if r["채점"]["계산기"] is False: why.append("계산기")
        if r["채점"]["근거recall"] not in (None, 1.0): why.append(f"근거{r['채점']['근거recall']:.1f}")
        L.append(f"- **{r['id']}** [{r['유형']}→{r['라우팅실제']}] {why} · {r['질의'][:46]}"
                 + (f" · 비고: {r['비고']}" if r["비고"] else ""))

    open(os.path.join(a.out, f"report_{tag}.md"), "w", encoding="utf-8").write("\n".join(L))
    print("\n" + "\n".join(L[:40]))
    print(f"\n→ {a.out}/report_{tag}.md, {a.out}/results_{tag}.jsonl")


if __name__ == "__main__":
    main()
