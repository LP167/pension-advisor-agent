# -*- coding: utf-8 -*-
"""투자설명서 100종 → 펀드 마스터 (L1 규칙추출 + L3 자동검증).
클래스 단위 보수·비용표는 파서 v2(세로쓰기 셀 복원)로 추출하고 실패 시 v1로 폴백한다.
출력: data/master/fund_master.json / fund_master.csv / _validation.md
"""
import os, re, json, glob, statistics
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TXT  = os.path.join(BASE, "data", "fund_text")
OUT  = os.path.join(BASE, "data", "master"); os.makedirs(OUT, exist_ok=True)

RISK_LABEL = {1:"매우 높은 위험",2:"높은 위험",3:"다소 높은 위험",4:"보통 위험",5:"낮은 위험",6:"매우 낮은 위험"}
def sp(s): return re.sub(r"\s+","",s or "")

def risk(t):
    h = t[:4000]
    for pat in [r"(\d)\s*등급\s*[\(\[]([^)\]]{2,10})[\)\]]", r"투자위험등급[^\n]{0,40}?(\d)\s*등급"]:
        m = re.search(pat, h)
        if m:
            g = int(m.group(1))
            if 1 <= g <= 6:
                lab = m.group(2).strip() if m.lastindex and m.lastindex >= 2 else RISK_LABEL[g]
                return g, re.sub(r"\s+"," ",lab)
    m = re.search(r"(\d)\s*등급으로\s*분류", h)
    if m: 
        g=int(m.group(1))
        if 1<=g<=6: return g, RISK_LABEL[g]
    return None, None

def fund_name(t):
    h = t[:6000]
    for pat in [r"집합투자기구\s*명칭[^\n]*?\n?\s*([가-힣A-Za-z0-9()\[\]\-·.,%\s]{6,90}?)\s*(?:\n|집합투자업자)",
                r"이\s*투자설명서는\s*(.{6,90}?)에\s*대한",
                r"1\.\s*집합투자기구\s*명칭\s+(.{6,90}?)\s*\n"]:
        m = re.search(pat, h, re.S)
        if m:
            n = re.sub(r"\s+","", m.group(1))
            n = n.strip(" :·")
            if 6 <= len(n) <= 80 and ("투자신탁" in n or "펀드" in n or "투자회사" in n or "증권" in n):
                return n
    return None

def manager(t):
    h=t[:6000]
    m = re.search(r"([가-힣A-Za-z0-9]{2,20}자산운용\s*(?:주식회사|㈜|\(주\))?)", h)
    if m: return re.sub(r"\s+","",m.group(1))
    m = re.search(r"집합투자업자\s*명칭\s*([^\n]{2,30})", h)
    return re.sub(r"\s+","",m.group(1)) if m else None

def as_of(t):
    h=re.sub(r"[ \t]+","", t[:12000])
    m = re.search(r"작성\s*기준일\s*[:\s]*((?:19|20)\d{2})\s*년?\s*(\d{1,2})\s*월?\s*(\d{1,2})\s*일?", h)
    if m: return "%s-%02d-%02d"%(m.group(1),int(m.group(2)),int(m.group(3)))
    m = re.search(r"작성\s*기준일[^\n]*?((?:19|20)\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", h)
    if m: return "%s-%02d-%02d"%(m.group(1),int(m.group(2)),int(m.group(3)))
    return None

def classify(t):
    m = re.search(r"분류\s{2,}(투자신탁[^\n]{5,160})", t)
    line = re.sub(r"\s+"," ",m.group(1)).strip() if m else None
    if not line:
        m = re.search(r"집합투자기구의?\s*구조\s*[:：]\s*([^\n]{5,160})", t)
        line = re.sub(r"\s+"," ",m.group(1)).strip() if m else None
    body = sp(line or t[:20000])
    asset = None
    m2 = re.search(r"증권\(([^)]{2,20})\)", body)
    if m2: asset = m2.group(1)
    else:
        for k in ["주식형","채권형","혼합형","재간접","부동산","특별자산","단기금융","MMF"]:
            if k in body: asset=k; break
    return {"분류원문": line, "자산유형": asset,
            "모자형": "모자형" in body, "종류형": "종류형" in body,
            "개방형": "개방형" in body, "추가형": "추가형" in body}

def benchmark(t):
    m = re.search(r"비교지수\s*[:：]\s*([^\n]{3,80})", t)
    return re.sub(r"\s+"," ",m.group(1)).strip() if m else None

def strategy(t):
    m = re.search(r"투자전략\s*\n(.{20,600}?)(?:\n\s*분류\s|\n\s*투자비용|\n\s{2,}투자자가 부담)", t, re.S)
    if not m: m = re.search(r"2\.\s*투자전략\s*\n+(.{20,600}?)\n\s*\n", t, re.S)
    if not m: return None
    s = re.sub(r"\s+"," ", m.group(1)).replace('“','"').replace('”','"').replace('„','"')
    return s.strip()[:400]

# ══════ 보수·비용 클래스표 파서 v2 ══════
# 간이투자설명서의 보수표는 PDF 추출 시 클래스명 셀이 세로로 쪼개지고 컬럼이 뒤섞인다.
# 1/2/3/5/10년 누적비용 예시(증가하는 정수열)를 행 앵커로 잡고, 클래스명은 행 사이 구간에서
# 종료기호 (클래스코드) 를 기준으로 회수한다. v2 실패 시 v1(classes_v1) 로 폴백한다.
V2_DEC = re.compile(r"(?<![\d.,])-?\d{1,3}(?:,\d{3})*\.\d{1,4}(?![\d])")
V2_NOTE= re.compile(r"^\s*[\(（]?\s*주\s*\d")
FRONTP= re.compile(r"(?:납입금액|환매금액)의?\s*([\d.]+)\s*%\s*(?:이내)?")
TERM  = re.compile(r"\d+\s*년\s*(?:미만|이상|이내)[^%]{0,12}%")
V2_CODE= re.compile(r"[\(（]\s*(C-P2|C-Pe|C-P|C-E|C-F|C-I|C-W|S-P2|S-P|A-e2|A-e|A-E|C-e2|C-e|Ae|Ce|C1|C2|C3|C4|C5|R-A|A|C|S|F|I|P|W|R)\s*[\)）]")
V2_TOK = re.compile(r"-|없음|(?<![\d.,])-?\d{1,3}(?:,\d{3})*\.\d{1,4}|(?<![\d.,])\d{1,3}(?:,\d{3})*(?![\d.])")

V2_HDR = ["선취판매수수료","판매수수료","총보수･비용","총보수·비용","총보수ㆍ비용","총보수","판매보수","동종유형",
       "클래스종류","클래스","투자비용","투자자가부담하는","수수료및비용","비용예시","단위:천원","단위:%",
       "투자기간별","1,000만원투자시","예시","이내","해당사항없음","없음","연평균","수익률","납입금액의","환매금액의"]
V2_NAMES=["수수료선취","수수료미징구","수수료후취","판매수수료선취","판매수수료미징구",
        "오프라인","온라인","슈퍼","개인연금","퇴직연금","기관","고액","연금저축","일반","직판"]

def prep(line):
    front = None
    m = FRONTP.search(line)
    if m:
        try: front = float(m.group(1))
        except ValueError: pass
        line = line[:m.start()] + " " + line[m.end():]
    line = TERM.sub(" ", line)
    return re.sub(r"(?<=[\d])\s*%", " ", line), front

def _cells(line):
    """토큰열을 (보수셀, 비용예시정수) 로 분리. 끝의 증가하는 정수열이 예시 컬럼."""
    clean, front = prep(line)
    m = V2_DEC.search(clean)
    if not m: return None
    toks = V2_TOK.findall(clean[m.start():])
    # 끝에서부터 연속된 정수 런
    run = []
    for t in reversed(toks):
        if t in ("-","없음") or "." in t: break
        run.append(t)
    run.reverse()
    val = [int(x.replace(",", "")) for x in run]
    if len(run) < 4: return None
    # 예시 컬럼은 1/2/3/5/10년 누적비용 → 증가하는 연속 구간. 표 밖 숫자가 꼬리에 붙는 경우가 있어
    # run 전체가 아니라 가장 긴 증가 구간(길이 4 이상)을 예시 컬럼으로 본다.
    best = None
    for a in range(len(run)):
        for b in range(a+4, len(run)+1):
            w = val[a:b]
            if all(w[i] <= w[i+1] for i in range(len(w)-1)) and w[-1] > w[0] and len(w) <= 5:
                if best is None or len(w) > len(best[2]) or (len(w) == len(best[2]) and a < best[0]): best = (a, b, w)
    if best is None: return None
    a, b, ex = best
    head = toks[:len(toks)-len(run)] + run[:a]
    fees = []
    for t in head:
        if t in ("-","없음"): fees.append(None); continue
        if "." in t: fees.append(float(t.replace(",",""))); continue
        v = int(t.replace(",",""))
        if v <= 6: fees.append(float(v)); continue
        if re.fullmatch(r"\d,\d{3}", t): fees.append(float(t.replace(",",".")))  # 소수점이 쉼표로 추출된 OCR 아티팩트
        else: return None
    while fees and fees[-1] is None: fees.pop()
    if len([f for f in fees if f is not None]) < 2: return None
    if any(f is not None and not (0 <= f <= 6) for f in fees): return None
    return fees, ex, front

def clean_name(seg):
    ns = re.sub(r"\s","",seg)
    for h in V2_HDR: ns = ns.replace(h,"|")
    out, i = [], 0
    while i < len(ns):
        for tk in sorted(V2_NAMES,key=len,reverse=True):
            if ns.startswith(tk,i):
                if not out or out[-1]!=tk: out.append(tk)
                i += len(tk); break
        else: i += 1
    return "-".join(out) if out else None

def assign(fees):
    f = fees + [None]*(5-len(fees)) if len(fees) < 5 else fees
    if len(fees) >= 5:  return f[0], f[1], f[2], f[3], f[4]
    총, 판, a, b = f[0], f[1], f[2], f[3]
    if len(fees) == 3 and a is not None and 총 is not None and 0 <= a-총 <= 0.2:
        return None, 총, 판, None, a          # 동종유형 컬럼 공란 → a 는 총보수·비용
    return None, 총, 판, a, b

def parse_block(block, base_off=0, full_text=""):
    """base_off/full_text 가 주어지면 각 행의 원문 페이지(폼피드 기준)를 함께 기록한다."""
    lines = block.split("\n")
    line_off, acc = [], 0
    for ln in lines:
        line_off.append(acc); acc += len(ln) + 1
    rows = []
    for i, ln in enumerate(lines):
        if V2_NOTE.match(ln): continue
        c = _cells(ln)
        if c: rows.append((i, c[0], c[1], c[2], ln))
    if not rows: return []
    out, carry = [], ""
    for k, (i, fees, ex, fr0, ln) in enumerate(rows):
        nxt = rows[k+1][0] if k+1 < len(rows) else len(lines)
        prev = rows[k-1][0] if k else -1
        prefix = re.sub(r"\s","", (V2_DEC.split(ln)[0] if V2_DEC.search(ln) else ln))
        head = (re.sub(r"\s","", "".join(lines[prev+1:i])) if k == 0 else carry) + prefix
        after = re.sub(r"\s","", "".join(lines[i+1:nxt]))
        cm = V2_CODE.search(head)
        if cm:
            name_src, code, carry = head[:cm.end()], cm.group(1), head[cm.end():] + after
        else:
            cm = V2_CODE.search(after)
            if cm: name_src, code, carry = head + after[:cm.end()], cm.group(1), after[cm.end():]
            else:  name_src, code, carry = head + after, None, ""
        선취, 총, 판, 동, 총비 = assign(fees)
        page = (full_text[:base_off + line_off[i]].count("\f") + 1) if full_text else None
        out.append({"클래스명": clean_name(name_src), "클래스코드": code, "페이지": page,
                    "선취판매수수료_pct": 선취 if 선취 is not None else fr0,
                    "총보수_pct": 총, "판매보수_pct": 판,
                    "동종유형총보수_pct": 동, "총보수비용_pct": 총비,
                    "투자기간별비용_천원": ex,
                    "원문": re.sub(r"\s+"," ",ln).strip()[:150]})
    return out

V2_ANCH= re.compile(r"투자\s*비용|투자자가\s*부담하는|클래스\s*종류|보수\s*및\s*수수료")
def classes_v2(t):
    best = []
    for m in V2_ANCH.finditer(t[:250000]):
        r = parse_block(t[m.start():m.start()+5000], base_off=m.start(), full_text=t)
        if len(r) > len(best): best = r
    return best

# ══════ 보수표 파서 v1 (폴백) ══════
NUMS = re.compile(r"-?\d+\.\d{1,2}")
CLASS_PAT = re.compile(r"(수수료선취|수수료미징구|판매수수료선취|판매수수료미징구|온라인|오프라인|개인연금|퇴직연금|기관|고액|재간접)")
CODE_PAT  = re.compile(r"\(\s*(A|C|Ae|Ce|C-P|C-P2|C-Pe|C-E|C-F|C-I|C-W|S|S-P|S-P2|F|I|P|P2|e|A-E|C1|C2|C3|C4|C5|Class ?[A-Z][\w-]*)\s*\)")

def classes_v1(t):
    """보수 블록: 숫자행을 앵커로 잡고 위/아래 줄에서 클래스명을 회수."""
    m = re.search(r"투자자가\s*부담하는\s*수수료(.{0,5000}?)(?:\(주\s*1|주1\)|최근\s*1\s*년)", t, re.S)
    if not m: return []
    lines = m.group(0).split("\n")
    out, seen = [], set()
    for i, ln in enumerate(lines):
        nums = NUMS.findall(ln)
        if len(nums) < 3: continue
        ctx = " ".join(lines[max(0,i-2):i+3])
        cm = CLASS_PAT.search(ln) or CLASS_PAT.search(ctx)
        if not cm: continue
        code = CODE_PAT.search(ln) or CODE_PAT.search(ctx)
        seg = " ".join(x for x in [lines[i-1] if i else "", ln, lines[i+1] if i+1<len(lines) else ""])
        seg = re.sub(r"\s+"," ",seg).strip()
        nm = "".join(re.findall(r"수수료선취|수수료미징구|판매수수료선취|판매수수료미징구|오프라인|온라인|개인연금|퇴직연금|기관|고액", seg))
        front = re.search(r"납입금액의\s*([\d.]+)\s*%", seg)
        vals = [float(x) for x in nums]
        key = (nm, code.group(1) if code else None, tuple(vals[:3]))
        if key in seen: continue
        seen.add(key)
        out.append({"클래스명": nm or None,
                    "클래스코드": code.group(1) if code else None,
                    "선취판매수수료_pct": float(front.group(1)) if front else None,
                    "총보수_pct": vals[0] if vals else None,
                    "판매보수_pct": vals[1] if len(vals)>1 else None,
                    "동종유형총보수_pct": vals[2] if len(vals)>2 else None,
                    "원문": seg[:160]})
    return out

def classes(t):
    """v2 우선, 실패 시 v1 폴백. 파서 버전을 레코드에 남긴다."""
    c2 = classes_v2(t)
    if c2: return c2, "v2"
    return classes_v1(t), "v1"

def retirement_only(t, cls):
    body = sp(t[:60000])
    if any((c.get("클래스코드") or "") in ("C-P2","S-P2") for c in cls): return True
    return "퇴직연금" in body

recs=[]
for f in sorted(glob.glob(os.path.join(TXT,"*.txt"))):
    isin = os.path.basename(f)[:-4]
    t = open(f, encoding="utf-8", errors="ignore").read()
    g, lab = risk(t); cl, pv = classes(t); c = classify(t)
    recs.append({
        "isin": isin, "펀드명": fund_name(t), "운용사": manager(t),
        "위험등급": g, "위험등급라벨": lab, "작성기준일": as_of(t),
        "자산유형": c["자산유형"], "분류원문": c["분류원문"],
        "모자형": c["모자형"], "종류형": c["종류형"],
        "벤치마크": benchmark(t), "투자전략요약": strategy(t),
        "퇴직연금관련": retirement_only(t, cl),
        "클래스수": len(cl), "클래스": cl, "보수표파서": pv,
        "보수표페이지": next((c.get("페이지") for c in cl if c.get("페이지")), None),
        "원문파일": f"투자설명서/{isin}/R2_{isin}.pdf", "총페이지문자수": len(t),
    })

json.dump(recs, open(os.path.join(OUT,"fund_master.json"),"w"), ensure_ascii=False, indent=1)

# ---------- L3 자동 검증 ----------
CORE = ["펀드명","운용사","위험등급","작성기준일","자산유형"]
miss = {k:[r["isin"] for r in recs if not r.get(k)] for k in CORE}
no_cls = [r["isin"] for r in recs if r["클래스수"]==0]
bad_risk = [r["isin"] for r in recs if r["위험등급"] and not (1<=r["위험등급"]<=6)]
dates = [r["작성기준일"] for r in recs if r["작성기준일"]]
# 동일 펀드명 다른 ISIN = 클래스/모자형 중복 후보
from collections import defaultdict, Counter
byname=defaultdict(list)
for r in recs:
    if r["펀드명"]: byname[r["펀드명"]].append(r["isin"])
dups={k:v for k,v in byname.items() if len(v)>1}

lines=["# 펀드 마스터 자동 검증 리포트","",f"- 총 {len(recs)}종",
       f"- 작성기준일 범위: {min(dates) if dates else '-'} ~ {max(dates) if dates else '-'} (총 {len(set(dates))}종류)",""]
lines.append("## 필드 결측")
for k,v in miss.items():
    lines.append(f"- **{k}**: 결측 {len(v)}건" + (f" → {', '.join(v[:15])}" if v else " ✅"))
lines.append("")
lines.append(f"## 보수·비용 클래스행 미파싱: {len(no_cls)}건")
if no_cls: lines.append("→ " + ", ".join(no_cls))
else: lines.append("→ 100종 전체 파싱 완료 ✅")
n_cls = sum(r["클래스수"] for r in recs)
n_fee = sum(1 for r in recs for c in r["클래스"] if c.get("총보수_pct") is not None)
n_ex  = sum(1 for r in recs for c in r["클래스"] if c.get("투자기간별비용_천원"))
lines.append(f"- 클래스행 총 {n_cls}행 · 총보수 확보 {n_fee}행 · 1000만원 투자시 기간별비용 확보 {n_ex}행")
lines.append("- 파서 버전: " + ", ".join(f"{k} {v}종" for k,v in sorted(Counter(r["보수표파서"] for r in recs).items())))
lines.append("")
lines.append(f"## 동일 펀드명 복수 ISIN (클래스·모자형 중복 후보): {len(dups)}건")
for k,v in list(dups.items())[:40]: lines.append(f"- {k} → {', '.join(v)}")
lines.append("")
lines.append("## 위험등급 분포")
for g,c in sorted(Counter(r["위험등급"] for r in recs).items(), key=lambda x:(x[0] is None, x[0])):
    lines.append(f"- {g}등급: {c}건")
open(os.path.join(OUT,"_validation.md"),"w").write("\n".join(lines))

import csv
with open(os.path.join(OUT,"fund_master.csv"),"w",newline="",encoding="utf-8-sig") as fp:
    w=csv.writer(fp); w.writerow(["isin","펀드명","운용사","위험등급","위험등급라벨","작성기준일","자산유형","모자형","종류형","클래스수","벤치마크","투자전략요약"])
    for r in recs: w.writerow([r[k] for k in ["isin","펀드명","운용사","위험등급","위험등급라벨","작성기준일","자산유형","모자형","종류형","클래스수","벤치마크","투자전략요약"]])
print("\n".join(lines[:60]))
