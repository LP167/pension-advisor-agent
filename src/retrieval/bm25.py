# -*- coding: utf-8 -*-
"""BM25 역색인. 외부 의존 없음 (재현성·도커 경량화 목적)."""
import math, json, pickle, os
from collections import Counter, defaultdict
from typing import List, Dict, Any, Optional
from .tokenize import tokens, query_terms, EXPAND_WEIGHT

# OCR 청크 사전확률. 1.0 = 감점 없음.
# 0.82 → 0.92 (2026-08-30). "OCR 청크는 노이즈일 것"이라는 감점 폭이 근거 없이 컸다.
# 정상 문서의 띄어쓰기를 지워 재색인하는 대조 실험에서 순위가 거의 안 변해(어절 139→90종이어도
# char n-gram 이 흡수) 전제가 반증됐다. 다만 완전 제거(1.0)는 dev-194 근거 recall 이
# 98.5%→97.8% 로 떨어져(OCR 청크가 정답 근거를 밀어냄) 0.92 로 완화한다.
#   0.82: dev 98.5% / OCR패널 0/5      0.92: dev 98.5% / OCR패널 1/5      1.0: dev 97.8% / OCR패널 1/5
OCR_PENALTY = float(os.getenv("OCR_PENALTY", "0.92"))

class BM25Index:
    def __init__(self, k1: float = 1.4, b: float = 0.72):
        self.k1, self.b = k1, b
        self.docs: List[Dict[str, Any]] = []
        self.inv: Dict[str, List] = defaultdict(list)   # term -> [(doc_idx, tf)]
        self.dl: List[int] = []
        self.avgdl = 0.0
        self.df: Dict[str, int] = {}

    def build(self, records: List[Dict[str, Any]]):
        self.docs = records
        df = Counter()
        for i, r in enumerate(records):
            tf = Counter(tokens(r["text"]))
            self.dl.append(sum(tf.values()) or 1)
            for t, c in tf.items():
                self.inv[t].append((i, c)); df[t] += 1
        self.df = dict(df)
        self.avgdl = sum(self.dl)/max(len(self.dl), 1)
        return self

    def search(self, query: str, top_k: int = 30,
               filter_fn=None, per_doc: int = None) -> List[Dict[str, Any]]:
        """per_doc: 한 문서에서 가져올 최대 청크 수.
        FAQ 문서(doc29)처럼 청크가 많은 문서가 상위를 독식해 다른 근거 문서를 밀어내는 것을 막는다."""
        # 어절(내용어) / n-gram / 확장 주입어의 무게를 분리한다.
        #  · n-gram 은 띄어쓰기가 깨진 OCR 을 건지는 보조 수단이지 그 자체로 주제를 지시하지 않는다.
        #  · 확장 주입어(고객어 사전)는 질의 자신의 낱말을 절대 이기면 안 된다. 이기는 순간
        #    검색이 아니라 특정 문서로의 배선이 된다 (2026-08-30: '살 수 있'→doc58 사고).
        q, words, expanded = query_terms(query)
        def wt(t):
            if t in expanded: return EXPAND_WEIGHT
            return 1.0 if t in words else (0.65 if len(t) >= 3 else 0.35)
        N = len(self.docs)
        scores = defaultdict(float)
        for t, qc in q.items():
            post = self.inv.get(t)
            if not post: continue
            n = self.df.get(t, 1)
            if n > N * 0.5: continue                       # 과빈출 n-gram 제거
            idf = math.log(1 + (N - n + 0.5)/(n + 0.5))
            for i, c in post:
                dl = self.dl[i]
                denom = c + self.k1*(1 - self.b + self.b*dl/self.avgdl)
                scores[i] += wt(t) * idf * (c*(self.k1+1))/denom
        # 추출 품질 사전확률.
        # OCR 감점(×0.82)은 2026-08-30 제거했다. "OCR 청크는 노이즈일 것"이라는 근거 없는 가정이었고,
        # 정상 문서의 띄어쓰기를 일부러 지워 재색인하는 대조 실험에서 순위가 거의 변하지 않아
        # (어절토큰 139→90종이어도 char n-gram 이 흡수) 전제가 반증됐다. 감점만 남아 이미지 PDF
        # 18종을 일괄 18% 깎고 있었다.
        for i in list(scores):
            if self.docs[i].get("source") == "ocr": scores[i] *= OCR_PENALTY
            elif self.docs[i].get("source") in ("faq", "table"): scores[i] *= 1.05
        items = sorted(scores.items(), key=lambda x: -x[1])
        out, seen = [], Counter()
        for i, s in items:
            r = self.docs[i]
            if filter_fn and not filter_fn(r): continue
            if per_doc:
                if seen[r["doc_id"]] >= per_doc: continue
                seen[r["doc_id"]] += 1
            out.append(dict(r, _score=round(s, 4)))
            if len(out) >= top_k: break
        return out

    def save(self, path): 
        with open(path, "wb") as f: pickle.dump(self, f)
    @staticmethod
    def load(path):
        with open(path, "rb") as f: return pickle.load(f)

def load_chunks(*paths) -> List[Dict[str, Any]]:
    recs = []
    for p in paths:
        if not os.path.exists(p): continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.strip(): recs.append(json.loads(line))
    return recs
