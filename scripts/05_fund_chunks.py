# -*- coding: utf-8 -*-
"""투자설명서 100종 → 청크. 요약정보(p1~6) + 제2부 핵심섹션만 인덱싱.
각 청크에 isin·펀드명·위험등급·작성기준일(as_of)을 메타로 부착 → 근거 필터·L2 팩트체크의 기준.
출력: data/chunks_fund.jsonl
"""
import os, re, json
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TXT  = os.path.join(BASE,"data","fund_text")
MAST = json.load(open(os.path.join(BASE,"data","master","fund_master.json"), encoding="utf-8"))
M = {r["isin"]: r for r in MAST}

KEY = re.compile(r"(투자목적|투자대상|투자전략|위험관리|투자위험|보수\s*및\s*수수료|이익\s*배분|과세|매입[,、·]?\s*환매|기준가격|집합투자기구의\s*구조|운용전문인력|집합투자기구의\s*연혁)")
def norm(s): return re.sub(r"\n{3,}","\n\n", re.sub(r"[ \t]{2,}"," ", s or "")).strip()
def nospace(s): return re.sub(r"(?<=[가-힣])\s+(?=[가-힣])","", s or "")

def split_size(text, size=1000, ov=150):
    text = norm(text)
    if len(text) <= size: return [text] if len(text) >= 30 else []
    out=[]; i=0
    while i < len(text):
        seg = text[i:i+size]
        if i+size < len(text):
            cut = max(seg.rfind("\n"), seg.rfind("다."))
            if cut > size*0.5: seg = seg[:cut+1]
        if len(seg.strip()) >= 30: out.append(seg.strip())
        i += max(len(seg)-ov, int(size*0.5))
    return out

n=0
with open(os.path.join(BASE,"data","chunks_fund.jsonl"),"w",encoding="utf-8") as fp:
    for isin, r in M.items():
        path = os.path.join(TXT, isin+".txt")
        if not os.path.exists(path): continue
        pages = open(path, encoding="utf-8", errors="ignore").read().split("\f")
        for pi, pg in enumerate(pages, 1):
            if pi > 6 and not KEY.search(pg): continue
            sec = None
            m = KEY.search(pg)
            if m: sec = m.group(1)
            elif pi <= 6: sec = "요약정보"
            for part in split_size(pg):
                n += 1
                fp.write(json.dumps({
                    "chunk_id": f"{isin}#p{pi}c{n}", "doc_id": isin, "page": pi,
                    "section": sec, "source": "fund",
                    "text": part, "text_ns": nospace(part),
                    "meta": {"isin": isin, "펀드명": r["펀드명"], "운용사": r["운용사"],
                             "위험등급": r["위험등급"], "자산유형": r["자산유형"],
                             "모자형": r["모자형"], "종류형": r["종류형"],
                             "as_of": r["작성기준일"],
                             "원문": f"투자설명서/{isin}/R2_{isin}.pdf p.{pi}"}
                }, ensure_ascii=False)+"\n")
print("fund chunks:", n)
