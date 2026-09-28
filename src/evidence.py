# -*- coding: utf-8 -*-
"""근거 필터 — 질의 대상과 무관하거나 대상이 다른 근거를 배제한다.
평가지표 '근거 완전성'은 recall뿐 아니라 precision도 채점한다(과제 PDF).
제외한 근거의 사유를 증명서 retrieval.rejected 에 남긴다.
"""
import os, re
from typing import List, Dict, Any, Tuple
from retrieval.tokenize import tokens as _tok

REASONS = ["대상불일치", "중복", "저점수", "사유불일치", "타펀드"]

WITHDRAW_KEY = {
    "주택구입": [r"주택\s*구입", r"집\s*사", r"매매", r"소유권\s*이전"],
    "전세보증금": [r"전세", r"임차", r"보증금", r"월세", r"전월세"],
    "요양": [r"요양", r"의료비", r"치료", r"질병", r"입원"],
    "재난": [r"재난", r"수해", r"화재", r"지진", r"침수"],
    "회생파산": [r"회생", r"파산", r"신용회복", r"워크아웃"],
}

def detect_withdraw_reason(q: str) -> str:
    for k, pats in WITHDRAW_KEY.items():
        if any(re.search(p, q) for p in pats): return k
    return None

def detect_fund_names(q: str, master: List[Dict[str, Any]]) -> List[str]:
    """질의에 등장한 펀드를 ISIN으로 식별 (공백 무시 부분일치)."""
    qn = re.sub(r"[\s·,]", "", q)
    hits = []
    for r in master:
        name = re.sub(r"[\s·,]", "", r.get("펀드명") or "")
        if not name: continue
        core = re.sub(r"(증권|자?투자신탁|제?\d+호|\(.*?\)|투자회사)", "", name)
        if len(core) >= 4 and core in qn:
            hits.append(r["isin"])
    return hits

def filter_evidence(q: str, qtype: str, cands: List[Dict[str, Any]],
                    master: List[Dict[str, Any]], top_k: int = 6,
                    isin_hint: List[str] = None) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    rejected = {r: 0 for r in REASONS}
    reason = detect_withdraw_reason(q)
    # 정형 검색(fundq)이 식별한 ISIN 을 우선한다. 이름 부분일치보다 정확하다.
    fund_isins = list(isin_hint or []) or detect_fund_names(q, master)
    kept, seen = [], set()

    is_product = qtype in ("상품설명비교", "조건부추천")
    for c in cands:
        # 1) 상품 질의인데 특정 펀드가 지목된 경우 → 다른 펀드 근거 배제
        if fund_isins and c["source"] == "fund" and c["doc_id"] not in fund_isins:
            rejected["타펀드"] += 1; continue
        # 2) 제도·세제 질의에 펀드 투자설명서 본문이 섞여 들어오는 것 배제
        if not is_product and c["source"] == "fund" and not fund_isins:
            rejected["대상불일치"] += 1; continue
        # 3) 중도인출 사유가 특정된 질의 → 다른 사유 문서 배제
        m = c.get("meta") or {}
        if reason and m.get("중도인출사유") and m["중도인출사유"] != reason:
            rejected["사유불일치"] += 1; continue
        # 4) 중복 제거 (앞 120자 기준)
        key = re.sub(r"\s", "", c["text"])[:120]
        if key in seen: rejected["중복"] += 1; continue
        seen.add(key)
        kept.append(c)
        if len(kept) >= top_k: break
    rejected["저점수"] = max(len(cands) - len(kept) - sum(v for k, v in rejected.items() if k != "저점수"), 0)
    return kept, {k: v for k, v in rejected.items() if v}

def _win() -> int:
    try: return int(os.getenv("MAX_CHUNK_CHARS", "420"))
    except ValueError: return 420

SENT = re.compile(r"(?<=[.!?。])\s|\n")

# 질의가 '특정 값'을 묻는 경우, 그 값이 실제로 들어 있는 구간에 가점을 준다.
# 어휘 밀도만 쓰면 같은 낱말이 반복되는 표 구간이 이기고, 정작 답이 실린 머리말이 잘린다.
# (벤치 BH-27: "(시행일 : 2024년 10월 31일)" 이 있는 앞부분이 버려지고
#  '실물이전'이 여러 번 나오는 신청경로·불가사유 표가 선택됐다)
ASK_VALUE = [
    (re.compile(r"언제|시행|시점|기준일|날짜|며칠|기한|언제부터"),
     re.compile(r"시행일|(19|20)\d{2}\s*[.\-년]|\d{1,2}\s*월\s*\d{1,2}\s*일")),
    (re.compile(r"얼마|몇\s|한도|비율|수수료|보수|세율|등급"),
     re.compile(r"\d[\d,]*\s*(원|만원|%|배|년|일|개월|등급)")),
]

def _value_pats(query: str):
    return [vp for qp, vp in ASK_VALUE if qp.search(query or "")]

def best_window(text: str, query: str, width: int = None) -> str:
    """청크에서 질의어 밀도가 가장 높은 구간만 남긴다.

    청크는 1,000자 단위로 잘려 있어 그중 실제로 질의에 답하는 부분은 일부다.
    전부 넘기면 근거 완전성의 precision 축에서 손해를 보고 context 도 불필요하게 길어진다.
    문장 경계에 맞춰 잘라 문맥이 끊기지 않게 한다."""
    width = width or _win()
    if not text or len(text) <= width: return text
    qt = {t for t in _tok(query, use_ngram=False, is_query=True) if len(t) >= 2}
    if not qt: return text[:width].rstrip() + " …"
    vps = _value_pats(query)
    units, pos = [], 0
    for part in SENT.split(text):
        if part is None: continue
        units.append((pos, part)); pos += len(part) + 1
    best, best_s = (0, width), -1.0
    for a in range(len(units)):
        st = units[a][0]; en = st
        for b in range(a, len(units)):
            en = units[b][0] + len(units[b][1])
            if en - st >= width: break
        seg = text[st:en]
        sc = sum(seg.count(t) for t in qt) / max(len(seg), 1) ** 0.5
        if vps and any(vp.search(seg) for vp in vps):
            sc *= 1.8          # 질의가 요구한 값의 형태가 실제로 들어 있는 구간
        if sc > best_s: best, best_s = (st, en), sc
    st, en = best
    seg = text[st:min(en, st + width)].strip()
    return ("… " if st > 0 else "") + seg + (" …" if en < len(text) else "")

def render_context(kept: List[Dict[str, Any]], start: int = 1, query: str = "") -> str:
    out = []
    for i, c in enumerate(kept, start):
        m = c.get("meta") or {}
        cite = m.get("원문") or f"{c['doc_id']} p.{c['page']}"
        head = f"[근거{i}] {cite}" + (f" · {c['section']}" if c.get("section") else "")
        if m.get("as_of"): head += f" · 작성기준일 {m['as_of']}"
        body = best_window(c["text"], query) if query else c["text"]
        out.append(head + "\n" + body)
    return "\n\n".join(out)
