# -*- coding: utf-8 -*-
"""검증 증명서 (Answer Assurance Certificate).
설명회 테크세션의 4단계 멀티레벨 검증(L0 형식 / L1 정책 / L2 팩트 / L3 목표충족)을 그대로 구현한다.
검증 실패 시 에이전트가 임의로 답하지 못하게 오케스트레이터가 즉시 정지한다.
"""
import re, json
import rules
from typing import Dict, Any, List, Optional

NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(%|퍼센트|만원|원|등급|년차|년|배수|세)?")
# 한국어 수 표기: "148만 5천원", "1억 2천만원" → 정수
KNUM = re.compile(r"(?:(\d+(?:,\d{3})*)\s*억)?\s*(?:(\d+(?:,\d{3})*)\s*만)?\s*(?:(\d+(?:,\d{3})*)\s*천)?\s*(?:(\d+(?:,\d{3})*))?\s*원")
def korean_numbers(text: str):
    out = set()
    for m in KNUM.finditer(text or ""):
        if not any(m.groups()): continue
        g = [int(x.replace(",", "")) if x else 0 for x in m.groups()]
        v = g[0]*100_000_000 + g[1]*10_000 + g[2]*1_000 + g[3]
        if v: out.add(float(v))
    return out

# 연도·날짜 표기는 수치 claim이 아니다.
# "2020년에 명칭이 변경" / "작성기준일 2025-02-07" 의 2020·2025·02·07 을 팩트체크에서 뺀다.
# 단 '60일 이내', '5년', '3개월' 같은 기간·규정값은 claim 으로 남긴다 (날짜가 아니다).
DATE = re.compile(
    r"(?:19|20)\d{2}\s*[.\-/]\s*\d{1,2}(?:\s*[.\-/]\s*\d{1,2})?"      # 2020.09.01 / 2025-02-07
    r"|(?:19|20)\d{2}\s*년(?:\s*\d{1,2}\s*월(?:\s*\d{1,2}\s*일)?)?"     # 2020년 / 2020년 9월 1일
    r"|\d{1,2}\s*월\s*\d{1,2}\s*일"                                     # 9월 1일
    r"|\d{1,2}\s*월(?!\s*(?:분|치|간|째|말|초|이내|이상|이하|미만|동안|납입|평균))"  # 9월
)
def _date_spans(text: str):
    """연도·날짜 표기가 차지하는 구간."""
    return [(m.start(), m.end()) for m in DATE.finditer(text or "")]

# 수치 claim이 아닌 것 — 목록 번호, 근거 번호, 각주
_NOT_CLAIM = [
    re.compile(r"^\s*\d+[.)]\s"),        # "2. 퇴직소득세 감면" 같은 목록 번호
    re.compile(r"\[근거\d+\]"),           # 근거 표기
    re.compile(r"^\s*[-*]\s*\d+[.)]\s"),
]
def _is_list_marker(ans: str, start: int, end: int) -> bool:
    """이 숫자 자체가 줄 머리의 목록 번호인지 판정.
    (줄이 목록으로 시작한다는 이유만으로 그 줄의 모든 숫자를 제외하면 안 된다)"""
    ls = ans.rfind("\n", 0, start) + 1
    before = ans[ls:start]                       # 숫자 앞부분
    after = ans[end:end + 2]                     # 숫자 뒤 1~2자
    return bool(re.fullmatch(r"\s*(?:[-*+]\s*)?", before)) and bool(re.match(r"[.)]\s", after))

def extract_claims(ans: str, skip_spans=None) -> List[Dict[str, Any]]:
    """답변에서 수치 claim을 단위·위치와 함께 추출 (L2 팩트체크 대상).
    skip_spans 는 claim 으로 보지 않을 구간(예: [근거3] 표기 자체)이다."""
    out = []
    dates = _date_spans(ans) + list(skip_spans or [])
    for m in NUM.finditer(ans):
        if any(a <= m.start() < b for a, b in dates):
            continue                                   # 연도·날짜 표기·근거번호는 claim 아님
        raw, unit = m.group(1), (m.group(2) or "")
        try: val = float(raw.replace(",", ""))
        except ValueError: continue
        if not unit and _is_list_marker(ans, m.start(), m.end()):
            continue                                   # 목록 번호는 claim 아님
        if re.match(r"^근거\d+$", ans[max(0, m.start()-2):m.end()+1].strip("[] ")):
            continue
        s = max(0, m.start()-35); ctx = re.sub(r"\s+", " ", ans[s:m.end()+15])
        out.append({"raw": m.group(0).strip(), "value": val, "unit": unit, "context": ctx,
                    "start": m.start(), "end": m.end()})
    return out

def _part_of_korean_number(cl, kn_answer, allowed) -> bool:
    """'148만 5천원'처럼 분해된 조각(148, 5)은 합성값이 허용집합에 있으면 통과."""
    ctx = cl.get("context", "")
    for m in KNUM.finditer(ctx):
        if not any(m.groups()): continue
        g = [int(x.replace(",", "")) if x else 0 for x in m.groups()]
        v = g[0]*100_000_000 + g[1]*10_000 + g[2]*1_000 + g[3]
        if v and v in allowed and cl["value"] in [float(x) for x in g if x]:
            return True
    return False

def _norm_nums(text: str):
    vals = set()
    for m in NUM.finditer(text or ""):
        try: vals.add(float(m.group(1).replace(",", "")))
        except ValueError: pass
    return vals

def l0_schema(payload: Dict[str, Any]) -> Dict[str, Any]:
    """4필드 스키마 검증. retrieved_context 는 역질문·차단 응답에서 정당하게 비므로
    '문자열로 존재할 것'만 요구하고, 비어 있다는 사실은 별도 플래그로 남긴다."""
    req = ["question_id", "question", "retrieved_context", "think_trace", "answer"]
    typed = [k for k in req if isinstance(payload.get(k), str)]
    nonempty = ["question_id", "question", "think_trace", "answer"]
    filled = [k for k in nonempty if isinstance(payload.get(k), str) and payload.get(k).strip()]
    return {"pass": len(typed) == len(req) and len(filled) == len(nonempty),
            "required_fields": len(req), "filled": len(filled),
            "missing": [k for k in nonempty if k not in filled] + [k for k in req if k not in typed],
            "context_empty": not (payload.get("retrieved_context") or "").strip()}

def l1_policy(out_check: Dict[str, Any], has_citation: bool) -> Dict[str, Any]:
    leak = out_check.get("leak_violation", 0)
    adv = out_check["advice_violation"]
    ok = adv == 0 and leak == 0 and has_citation
    # 'pass' 와 'block' 을 나눈다.
    #   단정적 투자권유·원금보장 → 문장 자체가 문제이므로 답변을 내보내지 않는다.
    #   프롬프트 골격 누출 → guard.strip_leaks 가 이미 골격을 걷어냈다.
    #     그런데도 답변을 통째로 버리면 정답과 계산까지 함께 버려진다(dev 실측 6문항,
    #     그중 B-015 는 세액공제 계산이 맞았는데 폐기됐다). 기록은 남기되 답변은 살린다.
    return {"pass": ok, "block": adv > 0, "banned_terms": 0, "advice_violation": adv,
            "leak_violation": leak, "leaks": out_check.get("leaks", []),
            "violations": out_check["violations"], "citation_present": has_citation}

# ── claim ↔ citation 대조 (§5-3 지적 3 / §6-3) ────────────────────────
# 기존 L2 는 근거 '전체'를 하나의 수 풀로 합쳐 두고 답변의 숫자가 그 안에 있기만 하면
# 통과시켰다. 그래서 다른 근거의 숫자를 끌어다 쓰거나(정답 맞고 근거 틀림),
# OCR 로 값이 소실된 구간을 인용하며 없는 숫자를 지어내도 4단계를 전부 통과했다.
# 답변의 각 문장은 자기가 인용한 [근거N] 안에서 검증되어야 한다.
CITE = re.compile(r"\[근거(\d+)\]")

def split_evidence_by_citation(evidence: str) -> Dict[int, str]:
    """render_context 가 만든 '[근거N] 출처\\n본문' 블록을 번호별로 되돌린다."""
    out: Dict[int, str] = {}
    ms = list(CITE.finditer(evidence or ""))
    for i, m in enumerate(ms):
        e = ms[i+1].start() if i+1 < len(ms) else len(evidence)
        n = int(m.group(1))
        out[n] = out.get(n, "") + evidence[m.end():e]
    return out

def _sentence_spans(text: str):
    """답변을 문장 단위 (start, end) 로 쪼갠다."""
    spans, start, n = [], 0, len(text or "")
    for m in re.finditer(r"[.!?]\s+|\n+", text or ""):
        spans.append((start, m.end())); start = m.end()
    if start < n: spans.append((start, n))
    return [(a, b) for a, b in spans if b > a]

def _nums_of(text: str):
    return _norm_nums(text) | korean_numbers(text)

def _match_value(v: float, allowed) -> bool:
    return (v in allowed) or (v*100 in allowed) or (v/100 in allowed) \
        or (v*10_000 in allowed) or (v*1_000 in allowed) \
        or any(abs(v-a) < 1e-6 for a in allowed)

# 접지 판정에 어절 토큰을 쓰면 한국어 활용형에 걸려 무너진다.
# ("근로자에게"·"지급해야"·"됩니다" 는 근거에 같은 내용이 있어도 표면형이 달라 안 맞는다)
# 문자 3-gram 포함률은 어미 변화를 타지 않으면서 없는 내용은 확실히 떨어뜨린다.
def _ngrams(text: str, n: int = 3):
    t = re.sub(r"[^0-9A-Za-z가-힣%]", "", text or "")
    if len(t) < n: return {t} if t else set()
    return {t[i:i+n] for i in range(len(t)-n+1)}

def sentence_grounding(sentence: str, chunk_text: str) -> float:
    """문장이 인용한 근거 안에 실제로 얹혀 있는 정도 (문자 3-gram 포함률)."""
    a = _ngrams(sentence)
    if not a: return 1.0
    return len(a & _ngrams(chunk_text)) / len(a)

# 근거에 접지될 수 없는 것이 정상인 문장 — 자료 한계 고지·역질문·검증 안내.
# 이건 과제 평가지표('정보한계 대응')가 오히려 요구하는 행동이라 접지 검사에서 뺀다.
META_SENT = re.compile(
    r"제공(?:된)?\s*자료|확인되지\s*않|확인할\s*수\s*없|확인이\s*필요|알려\s*?주시|알려주셔야"
    r"|참고하시기|직접\s*확인|검증\s*안내|근거\s*원문|추측으로|말씀해\s*주시|여쭤|되물"
    r"|답변드리기\s*어렵|제시되어\s*있지\s*않|명시되어\s*있지\s*않|나와\s*있지\s*않")

# dev 194문항 인용 문장 262개의 접지 분포는 연속적이라 깨끗한 분리점이 없다.
# 하나의 임계로 강등까지 밀면 29%가 경고를 달게 되어 가독성을 잃는다. 그래서 2단으로 나눈다.
#   MIN  미만 → 답변에 고지하고 강등 (사실상 근거 없이 말한 문장)
#   WARN 미만 → 증명서에만 기록. 답변은 건드리지 않고 think_trace 로 자체감사를 보여준다.
GROUNDING_MIN = 0.15
GROUNDING_WARN = 0.35
# 접지 검사를 걸 최소 문장 길이(공백 제외). 짧은 연결문은 대조 대상이 아니다.
GROUNDING_MIN_CHARS = 12

# §5 지적 2 — "가능"과 "유리"의 혼동.
# "IRP로 옮길 수 있나요"(가능)에 "유리합니다"(경제적 판단)로 넘어가면 안 된다.
# 절차 권유("확인해 보시길 추천드립니다")는 판단이 아니므로 넣지 않는다 —
# dev 실측에서 그것까지 잡으면 29%가 걸려 전부 오탐이었다.
JUDGMENT = re.compile(r"유리|불리|낫습니다|낫다|더\s*(?:좋|이득)|이득입니다|손해입니다"
                      r"|절세\s*효과가\s*(?:크|큽)")

def ungrounded_judgments(answer: str) -> List[Dict[str, Any]]:
    """근거 표기 없이 경제적 유불리를 단정한 문장."""
    raw = answer or ""
    out = []
    for a, b in _sentence_spans(raw):
        sent = raw[a:b]
        if not JUDGMENT.search(sent):      continue
        if META_SENT.search(sent):         continue
        if sent.rstrip().endswith("?"):    continue      # 근거에서 인용된 질문문
        if CITE.search(sent):              continue      # 근거를 댄 판단은 허용
        out.append({"sentence": re.sub(r"\s+", " ", sent).strip()[:120]})
    return out

# §5 지적 1 — 사실이 각각 맞아도 결론이 그 연결에 얹혀 있다는 보장은 없다.
# 결론 문장을 따로 뽑아 그것이 인용한 근거 위에 서 있는지 본다.
CONCLUSION_CUE = re.compile(r"^\s*(?:따라서|결론적으로|정리하(?:면|자면)|요약하(?:면|자면)|즉|그러므로)")
# 답변 말미의 정형 안내는 결론이 아니다.
TAIL_TEMPLATE = re.compile(r"다음\s*확인\s*사항|추가(?:로|적)[^.\n]{0,10}(?:정보|자료)가?\s*필요"
                           r"|문의하(?:시|여)|상담(?:을|하)|검증\s*안내|작성기준일")

def conclusion_check(answer: str, evidence: str) -> Dict[str, Any]:
    """결론 문장이 Premise→Evidence→Rule 연결 위에 실제로 얹혀 있는가."""
    raw = answer or ""
    chunks = split_evidence_by_citation(evidence)
    cands = []
    for a, b in _sentence_spans(raw):
        sent = raw[a:b]
        body = CITE.sub(" ", sent)
        if len(re.sub(r"\s", "", body)) < GROUNDING_MIN_CHARS: continue
        if META_SENT.search(body): continue
        cands.append((sent, body))
    if not cands:
        return {"판정": "결론 문장 없음"}
    # 결론 선택 순서: ① 접속사로 시작하는 마지막 문장 ② 근거를 인용한 마지막 문장
    # ③ 마지막 실질 문장. 말미 템플릿("다음 확인사항:")은 결론이 아니므로 후보에서 뺀다.
    cands = [c for c in cands if not TAIL_TEMPLATE.search(c[1])] or cands
    cued = [c for c in cands if CONCLUSION_CUE.search(c[1])]
    cited_sents = [c for c in cands if CITE.search(c[0])]
    sent, body = (cued[-1] if cued else (cited_sents[-1] if cited_sents else cands[-1]))
    cited = sorted({int(x) for x in CITE.findall(sent)} & set(chunks))
    if not cited:
        return {"문장": re.sub(r"\s+", " ", body).strip()[:140], "인용": [],
                "판정": "근거 미인용 — 결론이 검색 결과가 아니라 모델 판단에 기대고 있을 수 있음"}
    g = max(sentence_grounding(body, chunks[n]) for n in cited)
    return {"문장": re.sub(r"\s+", " ", body).strip()[:140], "인용": cited,
            "접지": round(g, 2),
            "판정": ("근거 위에 있음" if g >= GROUNDING_WARN else
                    "근거 연결 약함" if g >= GROUNDING_MIN else "근거에 얹혀 있지 않음")}

def l2_fact(answer: str, evidence: str, compute: List[Dict[str, Any]]) -> Dict[str, Any]:
    """답변의 수치 claim 을 '그 문장이 인용한 근거' 와 1:1 대조한다.
    근거 전체 풀 대조는 다른 근거의 숫자를 빌려 쓰는 것을 못 잡으므로 보조 판정으로만 둔다.
    수치가 없는 문장도 인용을 붙였다면 그 근거에 내용이 실제로 있는지 본다(절차 날조 대응)."""
    raw = answer or ""
    cite_spans = [(m.start(), m.end()) for m in CITE.finditer(raw)]
    chunks = split_evidence_by_citation(evidence)

    compute_allowed = set()
    for c in compute or []:
        compute_allowed |= _norm_nums(json.dumps(c.get("output", {}), ensure_ascii=False))
        compute_allowed |= _norm_nums(json.dumps(c.get("input", {}), ensure_ascii=False))
        for v in (c.get("output") or {}).values():
            if isinstance(v, (int, float)):
                compute_allowed.add(float(v)); compute_allowed.add(round(float(v)*100, 4))
    allowed = _nums_of(evidence) | compute_allowed
    chunk_nums = {n: (_nums_of(t) | compute_allowed) for n, t in chunks.items()}

    kn_answer = korean_numbers(raw)
    claims = extract_claims(raw, skip_spans=cite_spans)
    sents = _sentence_spans(raw)

    def _cited_in(sent: str):
        return sorted({int(x) for x in CITE.findall(sent)} & set(chunks))

    details, bad, cite_bad = [], 0, 0
    for cl in claims:
        v = cl["value"]
        match = _match_value(v, allowed) or _part_of_korean_number(cl, kn_answer, allowed)
        sent = next((raw[a:b] for a, b in sents if a <= cl["start"] < b), "")
        cited = _cited_in(sent)
        cite_ok = None
        if cited:
            local = set().union(*(chunk_nums[n] for n in cited))
            cite_ok = _match_value(v, local) or _part_of_korean_number(cl, kn_answer, local)
            if match and not cite_ok:
                cite_bad += 1        # 근거 풀에는 있지만 '인용한' 근거에는 없는 값
        if not match: bad += 1
        details.append({"claim": cl["raw"], "context": cl["context"], "match": match,
                        "cited": cited, "cite_match": cite_ok})

    ungrounded, weak = [], []
    for a, b in sents:
        sent = raw[a:b]
        cited = _cited_in(sent)
        if not cited: continue
        body = CITE.sub(" ", sent)
        if len(re.sub(r"\s", "", body)) < GROUNDING_MIN_CHARS: continue
        if META_SENT.search(body): continue          # 자료 한계 고지는 접지 대상이 아니다
        g = max(sentence_grounding(body, chunks[n]) for n in cited)
        if g < GROUNDING_WARN:
            rec = {"sentence": re.sub(r"\s+", " ", body).strip()[:120],
                   "cited": cited, "grounding": round(g, 2)}
            (ungrounded if g < GROUNDING_MIN else weak).append(rec)

    # 값이 아니라 '관계'가 뒤집힌 진술 (공제율↔최소요건, 운용주체 반전 등)
    rule_bad = rules.check_contradictions(raw)
    judg = ungrounded_judgments(raw)

    return {"pass": (bad == 0 and cite_bad == 0 and not ungrounded
                     and not rule_bad and not judg),
            "rule_violations": rule_bad,
            "ungrounded_judgment": judg,
            "claims_checked": len(claims), "mismatched": bad,
            "citation_mismatch": cite_bad, "ungrounded_count": len(ungrounded),
            "ungrounded": ungrounded[:5],
            "weak_grounding_count": len(weak), "weak_grounding": weak[:5],
            "details": details[:20]}

STOP = {"어떻게","무엇","뭔가요","뭐가","얼마","알려","주세요","되나요","인가요","다른가요","다릅니까",
        "가능","해주세요","궁금","설명","질문","말씀","부탁","싶어요","있나요","할까요","건가요","것인가요",
        "달라요","달라","다른","차이","같은","그리고","그런데","이거","저거","좀","제발","진짜","혹시",
        "원해요","원합니다","해서요","합쳐서요","입니다","이에요","예요","한가요","나요",
        # 요청 어투 — 요구사항의 '내용'이 아니라 '말투'라 대조 대상에서 뺀다
        "알려주세요","알려주","알려줘","말해주","가르쳐","해주","드릴까","드리나요","정리해","보여주",
        # 답변이 동의어(감면·절감·과세이연)로 답해도 어휘 대조가 실패하는 요청어
        "절세법","방법만","방법을"}

# 조사·보조사가 붙은 토큰을 내용어로 되돌린다: "절세법만" → "절세법", "명퇴수당을" → "명퇴수당"
JOSA = re.compile(r"(?:이랑|하고|까지|부터|보다|처럼|마다|조차|밖에|만|도|은|는|이|가|을|를|의|에|와|과|랑|요)$")
def _strip_josa(t: str) -> str:
    for _ in range(2):
        s2 = JOSA.sub("", t)
        if len(s2) < 2 or s2 == t: break   # 2글자 이하로 깎지 않는다 ("제도"→"제" 방지)
        t = s2
    return t

def _stopped(t: str) -> bool:
    return t in STOP or any(t.startswith(s0) for s0 in STOP)

def _content_tokens(r: str):
    toks = re.findall(r"[A-Za-z]{2,}|[가-힣]{2,}", r)
    out = []
    for t in toks:
        if _stopped(t): continue          # 원형이 이미 어투면 조사를 떼기 전에 버린다
        t = _strip_josa(t)                # ("합쳐서요"→"합쳐서"로 깎여 STOP을 빠져나가는 것 방지)
        if _stopped(t): continue
        out.append(t)
    return out

def _loose_in(tok: str, an: str) -> bool:
    """조사·어미를 감안한 느슨한 포함 판정."""
    if tok in an: return True
    for L in range(len(tok)-1, 1, -1):
        if tok[:L] in an: return True
    return False

REQ_SPLIT = re.compile(r"(?:그리고|또|및|,|\?|？|하고|이랑|랑)")
def l3_intent(question: str, answer: str, requirements: Optional[List[str]] = None) -> Dict[str, Any]:
    """질의가 요구한 사항을 누락 없이 포함했는가."""
    if requirements is None:
        reqs = [r.strip() for r in REQ_SPLIT.split(question) if len(r.strip()) >= 4]
    else:
        reqs = requirements
    answered, missing = [], []
    an = re.sub(r"\s", "", answer)
    for r in reqs:
        toks = [t for t in _content_tokens(r)]
        hit = sum(1 for t in toks if _loose_in(t, an))
        (answered if (not toks or hit/len(toks) >= 0.34) else missing).append(r)
    return {"pass": not missing, "requirements": len(reqs),
            "answered": len(answered), "missing": missing}

def build_certificate(*, payload, out_check, has_citation, evidence, compute, question, answer,
                      requirements=None) -> Dict[str, Any]:
    L0 = l0_schema(payload); L1 = l1_policy(out_check, has_citation)
    L2 = l2_fact(answer, evidence, compute); L3 = l3_intent(question, answer, requirements)
    passed = sum(1 for x in (L0, L1, L2, L3) if x["pass"])
    return {"L0_schema": L0, "L1_policy": L1, "L2_fact": L2, "L3_intent": L3,
            "levels_passed": passed, "all_pass": passed == 4}

def confidence(route, cert, n_evidence, clarify=False) -> float:
    if clarify: return 0.95
    base = 0.35 + 0.15*cert["levels_passed"]
    base += min(n_evidence, 6) * 0.02
    base *= (0.75 + 0.25*float(route.get("confidence") or 0.5))
    if not cert["L2_fact"]["pass"]:
        L2 = cert["L2_fact"]
        # 침묵 실패(자신만만하게 틀림)를 막으려면 인용 불일치·미접지도 같은 무게로 깎아야 한다.
        penal = (L2["mismatched"] + L2.get("citation_mismatch", 0) + L2.get("ungrounded_count", 0)
                 + 2 * len(L2.get("rule_violations") or [])    # 규칙 모순은 가중치를 더 준다
                 + len(L2.get("ungrounded_judgment") or []))
        base -= 0.10 * min(penal, 3)
    return round(max(0.05, min(base, 0.98)), 2)
