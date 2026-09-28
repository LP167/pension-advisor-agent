# -*- coding: utf-8 -*-
"""bench.jsonl 의 expect 를 run_*.json 결과와 대조해 채점한다.
must_contain 항목의 "A|B" 는 OR (하나만 맞으면 통과).
behavior 판정:
  역질문   → slot_action == clarify
  전제교정 → premise_detected == True
  차단     → stop in (injection,)
  한계고지 → stop in (no_evidence, personal_data) 또는 답변에 한계 문구
  직답     → 위 어느 것도 아님
"""
import json, glob, sys, re, collections

run = json.load(open(sorted(glob.glob("data/runs/run_*.json"))[-1]))
bench = {json.loads(l)["id"]: json.loads(l) for l in open("eval/bench.jsonl", encoding="utf-8")}
LIMIT_PAT = re.compile(r"확인되지 않|확인할 수 없|명시되어 있지 않|조회할 수 없|찾을 수 없|찾지 못|정보가 (없|포함되어 있지 않)|포함되어 있지 않|알 수 없습니다")

def norm(t):
    """표기 차이를 흡수한다. 모델은 SYSTEM 규칙4에 따라 '600만 원', '**40**%' 로 쓴다."""
    t = t.replace("*", "").replace("\u00a0", " ")
    t = re.sub(r"(\d)\s*,\s*(\d)", r"\1,\2", t)          # 5, 500 → 5,500
    t = re.sub(r"(\d[\d,]*)\s*만\s*원", r"\1만원", t)        # 600만 원 → 600만원
    t = re.sub(r"(\d[\d,]*)\s*원", r"\1원", t)
    t = re.sub(r"(\d[\d.]*)\s*%", r"\1%", t)
    t = re.sub(r"(\d)\s*(년|일|주|개월|등급|세)", r"\1\2", t)
    return t

def won(t):
    """9,000,000원 ↔ 900만원 상호 표기를 함께 담는다."""
    out = [t]
    for m in re.finditer(r"(\d[\d,]*)원", t):
        v = int(m.group(1).replace(",", ""))
        if v >= 10000 and v % 10000 == 0: out.append(f"{v//10000:,}만원")
    for m in re.finditer(r"(\d[\d,]*)만원", t):
        v = int(m.group(1).replace(",", "")) * 10000
        out.append(f"{v:,}원")
    return " ".join(out)

def hit(ans, key):
    a = won(norm(ans))
    return any(norm(alt).strip() in a for alt in key.split("|"))

def behavior_of(r, ans):
    """한계고지는 '답변 전체가 한계 고지'일 때만. SYSTEM 규칙7이 요구하는
    본문 중간의 '~는 확인되지 않습니다' 한 줄은 직답의 일부다."""
    if r["stop"] == "injection": return "차단"
    if r["slot_action"] == "clarify": return "역질문"
    if r["stop"] in ("no_evidence", "personal_data"): return "한계고지"
    # '가정:' / '다음 확인사항:' 블록은 SYSTEM 규칙8이 요구하는 꼬리말이라
    # 본문의 성격을 정하지 않는다. 잘라낸 뒤 도입부만 본다.
    body = re.split(r"가정\s*[::]|다음\s*확인\s*사항\s*[::]", ans)[0]
    if LIMIT_PAT.search(body[:130]): return "한계고지"
    if r["premise_detected"]: return "전제교정"
    return "직답"

rows = []
for r in run["results"]:
    b = bench[r["id"]]; e = b["expect"]; ans = r["answer"]
    beh = behavior_of(r, ans)
    mc = [(k, hit(ans, k)) for k in e["must_contain"]]
    mnc = [(k, norm(k) in norm(ans)) for k in e["must_not_contain"]]
    rows.append({
        "id": r["id"], "유형": b["유형"], "난이도": b["난이도"], "golden": b.get("golden"),
        "trap": b.get("trap"), "err": r["error"], "stop": r["stop"],
        "route_ok": r["route"] == e["route"], "route_got": r["route"], "route_exp": e["route"],
        "beh_ok": beh == e["behavior"] or (e["behavior"] == "한계고지" and beh in ("역질문",)),
        "beh_got": beh, "beh_exp": e["behavior"],
        "mc_ok": all(v for _, v in mc), "mc_miss": [k for k, v in mc if not v],
        "mnc_ok": not any(v for _, v in mnc), "mnc_hit": [k for k, v in mnc if v],
        "L": r["levels_passed"], "cert": r["cert"], "cite": r["has_citation"],
        "conf": r["confidence"], "ms": r["latency_ms"], "ctx": r["len_context"],
    })
    rows[-1]["all_ok"] = all([rows[-1]["route_ok"], rows[-1]["beh_ok"],
                              rows[-1]["mc_ok"], rows[-1]["mnc_ok"], not r["error"]])

# ── 행동 영향 라우팅 정확도 ──────────────────────────────────────────────
# 라우트는 두 가지 실행 분기만 바꾼다: ①검색 대상(상품 정형검색 vs 본문) ②슬롯 게이트.
# 그 둘이 바뀌지 않는 오라우팅(제도↔기타절차↔종합 사이 이동)은 답변 경로가 동일하므로
# 시스템 결함으로 세지 않는다. 실제로 동작이 달라지는 오라우팅만 집계한다.
PRODUCT = {"상품설명비교", "조건부추천"}      # 펀드 정형검색 분기
GATED   = {"조건부추천"}                     # 슬롯 역질문 게이트 분기
def behavioral(exp, got):
    """이 오라우팅이 실행 경로를 바꾸는가."""
    if exp == got: return False
    return (exp in PRODUCT) != (got in PRODUCT) or (exp in GATED) != (got in GATED)
for x in rows:
    x["route_behavioral_miss"] = behavioral(x["route_exp"], x["route_got"])
    x["route_ok_behavioral"] = not x["route_behavioral_miss"]
json.dump(rows, open("/tmp/_graded.json", "w"), ensure_ascii=False)

n = len(rows); pct = lambda k: f"{sum(1 for x in rows if x[k])}/{n} ({sum(1 for x in rows if x[k])/n*100:.0f}%)"
print(f"=== {run['meta']['llm']} / {run['meta']['model']} / {run['meta']['n']}건 / {run['meta']['total_sec']}초 ===")
for k, lbl in [("route_ok","라우팅(라벨 일치)"),("route_ok_behavioral","라우팅(행동 영향)"),
               ("beh_ok","기대동작"),("mc_ok","must_contain"),
               ("mnc_ok","must_not_contain"),("all_ok","전항목 통과")]:
    print(f"  {lbl:16} {pct(k)}")
print(f"  {'L0~L3 4단계':16} {sum(1 for x in rows if x['L']==4)}/{n}")
print(f"  {'에러':16} {sum(1 for x in rows if x['err'])}/{n}")

def block(title, key):
    print(f"\n--- {title} ---")
    agg = collections.defaultdict(lambda: [0,0,0,0,0])
    for x in rows:
        a = agg[x[key]]
        a[0]+=1; a[1]+=x["route_ok_behavioral"]; a[2]+=x["beh_ok"]; a[3]+=x["mc_ok"]; a[4]+=x["all_ok"]
    print(f"  {'':12} {'n':>3} {'라우팅*':>6} {'동작':>6} {'내용':>6} {'전체':>6}")
    for k, a in sorted(agg.items(), key=lambda kv: -kv[1][0]):
        print(f"  {str(k):12} {a[0]:>3} {a[1]:>6} {a[2]:>6} {a[3]:>6} {a[4]:>6}")
block("유형별", "유형"); block("난이도별", "난이도")

print("\n--- 골든 파생 30 vs 손작성 50 ---")
for lbl, sub in [("골든", [x for x in rows if x["golden"]]), ("손작성", [x for x in rows if not x["golden"]])]:
    m = len(sub)
    print(f"  {lbl:6} n={m:3} 라우팅 {sum(x['route_ok'] for x in sub):3} 동작 {sum(x['beh_ok'] for x in sub):3} "
          f"내용 {sum(x['mc_ok'] for x in sub):3} 전체 {sum(x['all_ok'] for x in sub):3}")

print("\n--- 함정별 ---")
tr = collections.defaultdict(lambda: [0,0])
for x in rows:
    if x["trap"]:
        k = x["trap"].split(" + ")[0].split("(")[0].strip()
        tr[k][0]+=1; tr[k][1]+=x["all_ok"]
for k,(a,b) in sorted(tr.items(), key=lambda kv:-kv[1][0]): print(f"  {k:26} {b}/{a}")

print("\n--- 행동 영향 오라우팅 (실행 경로가 바뀐 건) ---")
bm = [x for x in rows if x["route_behavioral_miss"]]
print(f"  {len(bm)}건" + ("" if bm else " — 없음"))
for x in bm:
    print(f"    {x['id']:6} {x['route_exp']} → {x['route_got']}   슬롯게이트={'변동' if (x['route_exp']=='조건부추천')!=(x['route_got']=='조건부추천') else '유지'}")

print("\n--- 실패 문항 ---")
for x in rows:
    if x["all_ok"]: continue
    why=[]
    if x["err"]: why.append(f"ERR {x['err']}")
    if not x["route_ok"]: why.append(f"라우팅 {x['route_exp']}→{x['route_got']}")
    if not x["beh_ok"]:  why.append(f"동작 {x['beh_exp']}→{x['beh_got']}")
    if not x["mc_ok"]:   why.append(f"누락 {x['mc_miss']}")
    if not x["mnc_ok"]:  why.append(f"금지어 {x['mnc_hit']}")
    print(f"  {x['id']:6} {x['유형']:8}{x['난이도']} L={x['L']} | " + " / ".join(why))
