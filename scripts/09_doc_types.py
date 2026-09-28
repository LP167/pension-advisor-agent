# -*- coding: utf-8 -*-
"""문서 → 질의유형 매핑 → data/doc_types.json

용도: 규칙 라우터가 아무 패턴도 못 맞췄을 때 '제도'로 하드코딩 폴백하는 대신,
검색된 문서들이 유형에 투표하게 한다. 규칙에 없는 어휘("장외채권 몇시까지 매수")도
그 답이 실린 문서(doc7 = 절차 매뉴얼)가 유형을 알려주므로 라우팅이 성립한다.

라벨 근거는 각 문서의 제목·주제이며 과제 taxonomy 를 따른다.
 · 제도    : DB/DC·IRP·연금저축 규칙, 운용 가능 범위·한도
 · 세제    : 세액공제·연금소득세·과세 구조
 · 기타절차 : 신청·이전·매매·인출 등 절차와 대응
 · 상품설명비교 : 투자설명서 기반 개별 펀드 (문서가 아니라 ISIN 청크가 담당)
복수 주제 문서는 보조유형에 0.5 가중치를 준다.
"""
import os, json, collections
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (primary, secondary) — secondary 는 0.5 가중
MAP = {
 "doc1": ("제도", None),        "doc2": ("기타절차", "세제"),  "doc3": ("기타절차", None),
 "doc4": ("기타절차", "세제"),   "doc5": ("기타절차", "세제"),  "doc6": ("기타절차", None),
 "doc7": ("기타절차", None),     "doc8": ("기타절차", None),    "doc9": ("기타절차", None),
 "doc10": ("제도", None),        "doc11": ("제도", None),       "doc12": ("제도", None),
 "doc13": ("제도", None),        "doc14": ("제도", None),       "doc15": ("제도", None),
 "doc16": ("제도", None),        "doc17": ("제도", None),       "doc18": ("제도", None),
 "doc19": ("세제", "기타절차"),   "doc20": ("기타절차", "세제"), "doc21": ("세제", None),
 "doc22": ("기타절차", None),    "doc23": ("세제", "기타절차"), "doc24": ("기타절차", None),
 "doc25": ("세제", None),        "doc26": ("세제", None),       "doc27": ("제도", "기타절차"),
 "doc28": ("기타절차", None),    "doc29": ("제도", "기타절차"), "doc30": ("제도", None),
 "doc31": ("제도", None),        "doc32": ("제도", None),       "doc33": ("기타절차", "세제"),
 "doc34": ("기타절차", None),    "doc35": ("기타절차", None),   "doc36": ("세제", None),
 "doc37": ("제도", "세제"),      "doc38": ("세제", None),       "doc39": ("세제", "제도"),
 "doc40": ("세제", None),        "doc41": ("세제", None),       "doc42": ("세제", None),
 "doc43": ("세제", None),        "doc44": ("세제", None),       "doc45": ("세제", None),
 "doc46": ("기타절차", "세제"),  "doc47": ("기타절차", "세제"), "doc48": ("기타절차", "세제"),
 "doc49": ("기타절차", "세제"),  "doc50": ("기타절차", "세제"), "doc51": ("세제", "제도"),
 "doc52": ("세제", None),        "doc53": ("제도", None),       "doc54": ("제도", "기타절차"),
 "doc55": ("기타절차", "제도"),  "doc56": ("기타절차", "제도"), "doc57": ("기타절차", None),
 "doc58": ("제도", None),
}

# 코퍼스에 실제로 존재하는 문서만 남긴다 (누락·오타 검출)
present = set()
with open(os.path.join(BASE, "data", "chunks.jsonl"), encoding="utf-8") as f:
    for line in f:
        if line.strip(): present.add(json.loads(line)["doc_id"])
missing = sorted(present - set(MAP), key=lambda x: int(x[3:]) if x[3:].isdigit() else 0)
extra   = sorted(set(MAP) - present, key=lambda x: int(x[3:]) if x[3:].isdigit() else 0)

out = {d: {"primary": p, "secondary": s} for d, (p, s) in MAP.items() if d in present}
json.dump(out, open(os.path.join(BASE, "data", "doc_types.json"), "w"), ensure_ascii=False, indent=1)

c = collections.Counter(v["primary"] for v in out.values())
print(f"문서 유형 매핑 {len(out)}종 → data/doc_types.json")
print("주유형 분포:", dict(c))
if missing: print("⚠️ 코퍼스에 있으나 매핑 누락:", missing)
if extra:   print("⚠️ 매핑에 있으나 코퍼스에 없음:", extra)
