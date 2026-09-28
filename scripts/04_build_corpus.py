# -*- coding: utf-8 -*-
"""코퍼스 빌드: raw_text + OCR → chunks.jsonl (+ 골든셋 / 룰테이블)
- 청크마다 doc_id·page 역참조 필수
- doc55 목차 계층 청킹 / doc46~50 중도인출사유 메타 승격 / doc29 골든셋 / doc34 룰테이블
"""
import os, re, json, glob
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW  = os.path.join(BASE,"data","raw_text"); OCR = os.path.join(BASE,"data","ocr_json")
OUT  = os.path.join(BASE,"data"); os.makedirs(OCR, exist_ok=True)

WITHDRAW = {  # doc46~50 : 본문 90% 동일, 사유만 다름 → 메타데이터 승격 (유사도 dedup 금지)
 "doc46": ("요양", "근로자 본인 또는 부양가족의 6개월 이상 요양"),
 "doc47": ("회생파산", "개인회생절차개시결정 또는 파산선고"),
 "doc48": ("전세보증금", "무주택 근로자의 주거목적 전세/임차보증금 부담"),
 "doc49": ("주택구입", "무주택 근로자 본인명의 주택 구입"),
 "doc50": ("재난", "재난으로 인한 피해"),
}
HEAD = re.compile(r"^\s*(?:■|□|◆|●|▶|【|<|제?\s*\d+\s*[장절]|\d+\.\s|[가-힣]\.\s|[IVXⅠ-Ⅹ]+\.\s)")

def norm(s): return re.sub(r"\n{3,}","\n\n", re.sub(r"[ \t]+"," ", s or "")).strip()
def nospace(s): return re.sub(r"(?<=[가-힣])\s+(?=[가-힣])","", s or "")

def fix_ocr_spacing(s):
    """tesseract 한국어는 글자 단위로 인식돼 '연 금 계 좌'처럼 나온다.
    한 줄에서 1글자 토큰 비율이 높으면 그 줄의 한글 사이 공백을 제거한다."""
    out=[]
    for ln in (s or "").split("\n"):
        toks=[t for t in ln.split() if t]
        if len(toks)>=4:
            single=sum(1 for t in toks if len(t)==1 and re.match(r"[가-힣]", t))
            if single/len(toks) > 0.45:
                ln = re.sub(r"(?<=[가-힣])\s+(?=[가-힣])", "", ln)
        out.append(ln)
    return "\n".join(out)

def split_size(text, size=900, ov=150):
    text = norm(text); out=[]; i=0
    if len(text) <= size: return [text] if text else []
    while i < len(text):
        seg = text[i:i+size]
        if i+size < len(text):
            cut = max(seg.rfind("\n"), seg.rfind(". "), seg.rfind("다."))
            if cut > size*0.5: seg = seg[:cut+1]
        out.append(seg.strip())
        i += max(len(seg)-ov, int(size*0.5))
    return [s for s in out if s]

def hier_chunks(text):
    """목차 기반 계층 청킹 (doc55 등 장문 매뉴얼)."""
    lines = text.split("\n"); secs=[]; cur={"path":[], "buf":[]}
    for ln in lines:
        if HEAD.match(ln) and len(ln.strip()) < 80:
            if cur["buf"]: secs.append({"path":list(cur["path"]), "text":"\n".join(cur["buf"])})
            lvl = 0 if re.match(r"^\s*(?:■|□|◆|●|▶|제?\s*\d+\s*[장절])", ln) else 1
            title = ln.strip()
            cur["path"] = ([title] if lvl==0 else (cur["path"][:1] + [title]))
            cur["buf"] = []
        else:
            if ln.strip(): cur["buf"].append(ln)
    if cur["buf"]: secs.append({"path":list(cur["path"]), "text":"\n".join(cur["buf"])})
    return [s for s in secs if s["text"].strip()]

chunks=[]; cid=0
def add(doc_id, page, text, section=None, meta=None, source="text"):
    global cid
    t = norm(text)
    if len(t) < 15: return
    cid += 1
    chunks.append({"chunk_id": f"{doc_id}#c{cid}", "doc_id": doc_id, "page": page,
                   "section": section, "source": source,
                   "text": t, "text_ns": nospace(t), "meta": meta or {}})

# ---------- 1. 일반 문서 ----------
for f in sorted(glob.glob(os.path.join(RAW,"*.json"))):
    j = json.load(open(f, encoding="utf-8")); d = j["doc_id"]
    if d in ("doc29","doc34"): continue
    if j.get("is_image_pdf"): continue                  # OCR 결과로 대체
    meta_base = {}
    if d in WITHDRAW:
        meta_base = {"주제":"중도인출", "중도인출사유": WITHDRAW[d][0], "사유설명": WITHDRAW[d][1]}
    if d == "doc55":
        for s in hier_chunks(j["pages"][0]["text"]):
            for part in split_size(s["text"], 1100, 150):
                add(d, 1, part, section=" > ".join(s["path"]), meta={"문서유형":"퇴직연금 실무 매뉴얼"})
    else:
        for pg in j["pages"]:
            for part in split_size(pg["text"]):
                add(d, pg["page"], part, meta=dict(meta_base))
    for t in j.get("tables", []):
        rows = t["rows"][:80]
        if not rows: continue
        txt = "\n".join(" | ".join((c or "").strip() for c in r) for r in rows if any((c or "").strip() for c in r))
        for part in split_size(txt, 1200, 100):
            add(d, t.get("page") or 1, part, section=f"표{t.get('idx')}", meta=dict(meta_base, 형식="표"), source="table")

# ---------- 2. OCR 문서 ----------
for f in sorted(glob.glob(os.path.join(OCR,"*.json"))):
    j = json.load(open(f, encoding="utf-8")); d = j["doc_id"]
    for pg in j["pages"]:
        for part in split_size(fix_ocr_spacing(pg["text"])):
            add(d, pg["page"], part, meta={"추출":"OCR"}, source="ocr")

# ---------- 3. doc29 → 골든셋 + FAQ 청크 ----------
golden=[]
j = json.load(open(os.path.join(RAW,"doc29.json"), encoding="utf-8"))
faq = next((t for t in j["tables"] if t.get("sheet")=="FAQ_100"), None)
src = next((t for t in j["tables"] if t.get("sheet")=="Sources"), None)
sources = {r[0]: {"설명": r[1], "URL": r[2]} for r in (src["rows"][1:] if src else []) if r and r[0]}
if faq:
    hdr = faq["rows"][0]
    for r in faq["rows"][1:]:
        if not r or not r[0]: continue
        rec = dict(zip(hdr, r))
        golden.append({"id": f"G-{rec.get('ID')}", "카테고리": rec.get("카테고리"),
                       "질문": rec.get("대표질문"), "유사질문": [x.strip() for x in (rec.get("유사질문표현") or "").split("/") if x.strip()],
                       "표준답변": rec.get("표준답변"), "AI분기포인트": rec.get("AI분기포인트"),
                       "근거구분": rec.get("근거구분"), "근거ID": rec.get("근거ID")})
        add("doc29", 1, f"[Q] {rec.get('대표질문')}\n[A] {rec.get('표준답변')}",
            section=rec.get("카테고리"),
            meta={"주제":"디폴트옵션 FAQ","골든셋ID":f"G-{rec.get('ID')}","근거ID":rec.get("근거ID")}, source="faq")
json.dump({"sources":sources,"items":golden}, open(os.path.join(OUT,"golden_set.json"),"w"), ensure_ascii=False, indent=1)

# ---------- 4. doc34 → 룰테이블 ----------
j = json.load(open(os.path.join(RAW,"doc34.json"), encoding="utf-8"))
rows = j["tables"][0]["rows"] if j["tables"] else []
rows = [r for r in rows if any((c or "").strip() for c in r)]
json.dump({"title":"실물이전 불가사유","rows":rows}, open(os.path.join(OUT,"rule_tables.json"),"w"), ensure_ascii=False, indent=1)
for part in split_size("\n".join(" | ".join((c or "").strip() for c in r) for r in rows), 1200, 100):
    add("doc34", 1, part, section="실물이전 불가사유", meta={"주제":"실물이전","형식":"룰테이블"}, source="table")

with open(os.path.join(OUT,"chunks.jsonl"),"w",encoding="utf-8") as fp:
    for c in chunks: fp.write(json.dumps(c, ensure_ascii=False)+"\n")

from collections import Counter
print("chunks:", len(chunks))
print("by source:", dict(Counter(c["source"] for c in chunks)))
print("docs covered:", len(set(c["doc_id"] for c in chunks)))
print("페이지역참조 결측:", sum(1 for c in chunks if not c["page"]))
print("golden set:", len(golden), "| rule rows:", len(rows))
