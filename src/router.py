# -*- coding: utf-8 -*-
"""의도 라우터 — 과제 PDF 질의 taxonomy와 1:1.
주제1 대고객 연금 질의: 제도 / 세제 / 종합 / 기타(절차)
주제2 상품 설명 고객 질의: 상품설명비교 / 조건부추천
규칙 1차 → 애매하면 LLM 2차. 규칙 히트를 증명서에 남긴다.
"""
import re
from typing import Dict, Any, List

from slots import wants_advice as _advice   # 라우터·게이트가 같은 신호를 공유한다

# 엔티티는 어떤 질의에나 등장하므로 도메인 가중치를 주지 않는다 (종합 오분류 방지)
ENTITY = r"(IRP|개인형퇴직연금|연금저축|\bDB\b|\bDC\b|확정급여|확정기여|퇴직연금|ISA|디폴트\s*옵션|사전지정운용|TDF)"

RULES = [
    ("조건부추천", [r"추천\s*(해|좀|해줘|해\s*주|부탁|받)", r"뭐가?\s*(좋|나을|제일|최고)",
                  r"어떤\s*(상품|펀드|계좌).{0,12}(좋|나을|할까|있나|보|찾|담|넣)", r"골라\s*(줘|주)",
                  r"가입\s*할까", r"뭘\s*사", r"괜찮은\s*(상품|펀드)",
                  # 니즈만 제시하고 상품 탐색을 요청하는 형태 (특정 상품 지목 없음)
                  r"(굴리|운용하|담|넣|투자하)고\s*싶", r"(넣을|굴릴|운용할|투자할)\s*(건데|예정|생각)",
                  r"(안전|안정)한\s*(상품|펀드|것)", r"(상품|펀드)\s*(뭐가?|어떤\s*게)?\s*있(나요|을까|습니까)",
                  r"(상품|펀드)을?\s*(보고\s*싶|보여\s*주|찾고\s*있|알아보)",
                  r"(넣어두|담아두|묻어두)고\s*싶", r"뭐\s*(사|살까|해야)",
                  r"수익률\s*(좋|높은).{0,12}(뭐|어떤|추천)",
                  # 벤치 BH-45/46/48/49 — 추천 요구인데 조건부추천으로 안 잡히던 형태
                  r"골라\s*(줘|주|야)", r"뭘\s*골라", r"어떤\s*(상품|펀드|유형|것|게|걸)\S{0,6}\s*(좋|맞|나을|골라|봐야|사야)",
                  r"무엇을?\s*(사|봐야|담)", r"(담|넣|사)\S{0,3}면\s*좋",
                  r"어떻게\s*구성", r"(제일|가장)\s*(유리|좋)"]),
    ("상품설명비교", [r"위험\s*등급", r"총?\s*보수", r"수익률", r"듀레이션", r"벤치마크", r"비교\s*해",
                   r"클래스", r"작성\s*기준일", r"비교\s*지수", r"선취\s*판매\s*수수료", r"환매\s*수수료",
                   r"\d[\d,]*\s*만원.{0,12}(비용|보수|수수료)", r"(누적|총)\s*비용",
                   r"(펀드|투자신탁|국공채|채권형|주식형)\S{0,10}(차이|다른가요|다릅|달라|비교|설명)",
                   r"(단기|중장기|장기|초단기).{0,12}(차이|다른가요|달라|비교)"]),
    ("세제",      [r"세액\s*공제", r"연금소득세", r"퇴직소득세", r"기타소득세", r"종합\s*과세", r"분리\s*과세",
                  r"세법", r"납입\s*한도", r"얼마까지\s*(납입|넣|낼)",
                  r"(납입|세액\s*공제|공제)\s*한도", r"세제\s*혜택", r"절세\s*혜택", r"과세\s*이연", r"건강보험료",
                  r"절세", r"세금", r"과세(?!\s*재원)", r"세율", r"연말정산", r"소득공제", r"감면", r"비과세", r"이연", r"연금\s*수령\s*한도", r"수령\s*연차", r"연금\s*외\s*수령", r"퇴직소득\s*한도"]),
    ("기타절차",   [r"중도\s*인출", r"실물\s*이전", r"현물\s*이전", r"계약\s*이전", r"서류",
                  r"신청\s*(방법|시기|기준|절차|하려|하면|하나요)", r"(?<!정)(?<!면)해지", r"개설", r"등록", r"옵트인",
                  r"절차", r"어떻게\s*하(나요|면)", r"의무\s*이전", r"납입\s*방법",
                  r"입금", r"인출", r"출금", r"가져오", r"꺼내|꺼낼", r"이전\s*(신청|방법|가능)",
                  r"언제\s*(받|지급|나오|신청)", r"(급여|퇴직금|연금|적립금|IRP로)\s*\S{0,6}받아야\s*하", r"수령\s*(신청|방법|시기|절차)",
                  r"과세\s*재원", r"재원\s*확정",
                  r"주문", r"체결", r"매수", r"매도", r"매매", r"조회", r"바꾸|바꿔",
                  r"(운용방법|상품|계좌|비밀번호|운용지시)\s*변경",
                  r"만들려|만들면|개설하려", r"필요해요|필요한가요|뭐\s*필요",
                  r"옮겨|가져오|가져올", r"언제\s*(들어오|들어와|나와|나오)",
                  r"어디서\s*(하|신청|확인)", r"몇\s*시", r"가능\s*시간", r"영업일"]),
    ("제도",      [r"운용\s*주체", r"가입\s*대상", r"가입\s*자격", r"규약", r"제도\s*(변경|차이|란|가|는)",
                  r"압류", r"가입자\s*교육", r"교육\s*(대상|주기|의무|받아야)",
                  r"가입할\s*수\s*있", r"가입\s*가능", r"지연이자",
                  r"위험자산", r"(투자|편입|집중투자)\s*한도", r"투자\s*가능",
                  r"못\s*사|살\s*수\s*있|담을\s*수\s*있|편입\s*가능",
                  r"운용\s*가능", r"적립기|인출기",
                  r"(퇴직금|급여|부담금).{0,12}(정해지|산정|계산\s*방식|결정)",
                  ENTITY + r"\S{0,12}(차이|다른가요|다릅|달라|무엇|뭔가요|이란|란\s*무엇)",
                  r"디폴트\s*옵션|사전지정운용", r"수급\s*요건", r"지급\s*요건"]),
]
# 약한 패턴 — 그 자체로는 도메인을 확정하지 못한다. "실물이전 제도가 무엇인가요"의 '제도가' 처럼
# 다른 도메인의 구체 키워드와 함께 등장하면 복합질의로 오판하게 만든다.
WEAK = {r"제도\s*(변경|차이|란|가|는)"}

COMPLEX = [r"(이랑|랑|하고|와|과|그리고|또|및|겸해서).{0,25}(알려|비교|어떻게|얼마|되나요)", r"[?？].{3,}[?？]"]
# 절 분리자 — "A랑 B까지", "A, 그리고 B" 형태의 복합 요구를 절 단위로 본다
SEG = re.compile(r",|그리고|및|아울러|(?<=[가-힣])랑\s|이랑\s|(?<=[가-힣])하고\s|(?<=[가-힣])와\s|(?<=[가-힣])과\s")
DOMAIN = ("제도", "세제", "기타절차")

def _groups(text: str, strong_only: bool = False):
    out = set()
    for name, pats in RULES:
        ps = [p for p in pats if not (strong_only and p in WEAK)]
        if any(re.search(p, text, re.I) for p in ps): out.add(name)
    return out

def rule_route(q: str) -> Dict[str, Any]:
    hits = {}
    for name, pats in RULES:
        h = [p for p in pats if re.search(p, q, re.I)]
        if h: hits[name] = h
    strong = {k: [p for p in v if p not in WEAK] for k, v in hits.items()}
    n_domain = len([k for k in DOMAIN if strong.get(k)])
    complex_hit = [p for p in COMPLEX if re.search(p, q)]
    # 절 단위 판정: 서로 다른 도메인을 요구하는 절이 2개 이상이면 복합질의
    segs = [x.strip() for x in SEG.split(q) if len(x.strip()) >= 5]
    seg_groups = [_groups(x, strong_only=True) & set(DOMAIN) for x in segs]
    seg_hit = [g for g in seg_groups if g]
    multi_seg = len(seg_hit) >= 2 and len(set().union(*seg_hit)) >= 2
    # 추천 요구는 도메인 개수와 무관하게 조건부추천이다.
    # ("옵트인으로 디폴트옵션 사려는데 어떤 유형을 골라야 하나요" → 제도+기타절차 2도메인이지만
    #  실제로 필요한 동작은 슬롯 역질문이다)
    if "조건부추천" in hits and _advice(q):
        t = "조건부추천"
    elif n_domain >= 2 or multi_seg:
        t = "종합"
    else:
        t = next((n for n, _ in RULES if n in hits), None)
    conf = 0.0
    if t == "조건부추천" and _advice(q): conf = 0.8
    elif t == "종합": conf = 0.75 if n_domain >= 2 else 0.7
    elif t: conf = min(0.55 + 0.12*len(hits.get(t, [])), 0.95)
    return {"type": t, "confidence": round(conf, 2),
            "rule_hits": {k: len(v) for k, v in hits.items()},
            "strong_domains": [k for k in DOMAIN if strong.get(k)],
            "segments": len(segs), "multi_segment": multi_seg,
            "complex": bool(complex_hit)}

# ── 근거 기반 라우팅 ────────────────────────────────────────────────────
# 규칙이 아무것도 못 맞추면 지금까지는 '제도'로 하드코딩 폴백했다. 그러면 규칙에 없는 어휘가
# 들어올 때마다 조용히 제도로 흘러간다(held-out 40문항 중 20문항이 이 경로였다).
# 대신 검색된 문서가 유형에 투표하게 한다 — 답이 실린 문서가 유형을 알고 있다.
def evidence_vote(cands, doc_types, top: int = 8):
    """cands: BM25 상위 후보(dict list). 반환 (유형, 신뢰도, 표)."""
    score = {}
    for rank, c in enumerate(cands[:top]):
        if c.get("source") == "fund":
            score["상품설명비교"] = score.get("상품설명비교", 0.0) + 1.0 / (rank + 2)
            continue
        t = doc_types.get(c.get("doc_id"))
        if not t: continue
        w = 1.0 / (rank + 2)
        score[t["primary"]] = score.get(t["primary"], 0.0) + w
        if t.get("secondary"): score[t["secondary"]] = score.get(t["secondary"], 0.0) + w * 0.5
    if not score: return None, 0.0, {}
    total = sum(score.values())
    best = max(score, key=score.get)
    return best, round(score[best] / total, 2), {k: round(v, 3) for k, v in score.items()}


PROMPT = """다음 질의를 아래 6가지 중 하나로 분류하고 그 이름만 출력하라.
제도 / 세제 / 종합 / 기타절차 / 상품설명비교 / 조건부추천
질의: {q}
분류:"""

def route(q: str, llm=None, cands=None, doc_types=None) -> Dict[str, Any]:
    r = rule_route(q)
    if r["type"] and r["confidence"] >= 0.67:
        r["by"] = "rule"; return r
    # 규칙 미확정 → 근거 투표. LLM 호출보다 싸고, 코퍼스에 실제로 있는 내용을 반영한다.
    if cands and doc_types:
        t, conf, tally = evidence_vote(cands, doc_types)
        if t and conf >= 0.4:
            return {"type": t, "confidence": round(min(0.55 + conf * 0.3, 0.85), 2),
                    "rule_hits": r["rule_hits"], "by": "evidence", "vote": tally,
                    "complex": r.get("complex", False)}
    if llm is not None:
        try:
            out = llm.chat([{"role": "user", "content": PROMPT.format(q=q)}], temperature=0.0, max_tokens=16)
            for name in ["조건부추천","상품설명비교","기타절차","종합","세제","제도"]:
                if name in out:
                    return {"type": name, "confidence": 0.7, "rule_hits": r["rule_hits"], "by": "llm", "complex": r["complex"]}
        except Exception:
            pass
    r["type"] = r["type"] or "제도"; r["by"] = "rule-fallback"
    return r
