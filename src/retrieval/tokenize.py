# -*- coding: utf-8 -*-
"""한국어 토크나이저 — 형태소 분석기 비의존.
OCR 결과는 어절 띄어쓰기가 깨져 있으므로(문자 단위 인식), 공백 제거 문자 n-gram을 병행한다.
"""
import re
HANGUL = re.compile(r"[가-힣]+")
WORD   = re.compile(r"[A-Za-z]{2,}|\d+(?:[.,]\d+)?%?|[가-힣]{2,}")

# 도메인 축약어·은어 정규화 (평가 질의에 구어체가 섞인다)
SYNONYM = {
    "연저펀": "연금저축펀드", "연저": "연금저축", "디씨": "DC 확정기여형", "디비": "DB 확정급여형",
    "아이알피": "IRP 개인형퇴직연금", "명퇴수당": "명예퇴직수당", "명퇴금": "명예퇴직수당",
    "옵트인": "옵트인 디폴트옵션 사전지정운용", "디폴트옵션": "디폴트옵션 사전지정운용제도",
    "실물이전": "실물이전 현물이전", "중도인출": "중도인출 중간정산",
    "세액공제": "세액공제 연말정산", "퇴직연금": "퇴직연금 DB DC IRP",
    "티디에프": "TDF 타겟데이트펀드", "isa": "ISA 개인종합자산관리계좌",
}
# 영문 약어 → 한글 정식명칭 (BM25가 정의 문서를 찾도록)
ACRONYM = {
    r"\bDC\b": "확정기여형 퇴직연금 DC제도",
    r"\bDB\b": "확정급여형 퇴직연금 DB제도",
    r"\bIRP\b": "개인형퇴직연금 IRP제도",
    r"\bTDF\b": "타겟데이트펀드 TDF",
    r"\bISA\b": "개인종합자산관리계좌 ISA",
}

# 의도 판정(라우터·슬롯) 전용 축약어 정규화.
# 검색용 SYNONYM 은 재현율을 위해 "퇴직연금 → 퇴직연금 DB DC IRP" 처럼 넓게 '확장'하는데,
# 그 확장을 슬롯 판정에 쓰면 '퇴직연금' 한 단어가 계좌유형 슬롯을 채워버린다.
# 그래서 여기서는 확장이 아니라 1:1 '치환'만 한다.
INTENT_ABBR = {
    "연저펀": "연금저축펀드", "연저": "연금저축", "디씨": "DC", "디비": "DB",
    "아이알피": "IRP", "명퇴수당": "명예퇴직수당", "명퇴금": "명예퇴직수당",
    "명퇴": "명예퇴직", "티디에프": "TDF", "퇴연": "퇴직연금",
}
def normalize_intent(q: str) -> str:
    """축약어를 정식 표기로 치환한다. 라우터·슬롯·전제검증이 공통으로 쓴다."""
    out = q or ""
    for k, v in sorted(INTENT_ABBR.items(), key=lambda kv: -len(kv[0])):
        if k in out: out = out.replace(k, v)
    return out

# 고객어 → 실무·법령어 확장. **질의에만** 적용한다(색인 재구축 불필요).
#
# 【2026-08-30】문제는 트리거의 존재가 아니라 **가중치**였다.
# 확장어가 가중치 1.0 으로 들어가면 질의 자신의 낱말을 이긴다. 실제로 "리츠도 살 수 있어요?" 에서
# 주입어 '운용방법'(기여 6.59)이 df=23 짜리 정답어 '리츠'(2.19)를 눌러 doc58 로 배선됐다.
# EXPAND_WEIGHT 로 감쇠하면 그 역전이 사라지고, 트리거는 재현율에만 기여한다.
# 사전 축소(일반 동사구 트리거 제거)도 시험했으나 dev-194 근거 recall 98.5%→96.3%,
# 대조군 7/7→5/7 로 손해였다. 감쇠만 적용하고 사전은 유지한다.
#
# 【2026-08-30 축소】최초 버전은 "살 수 있|팔 수 있|만들려" 같은 **일반 동사구**까지 트리거로 삼았다.
# 그 결과 "퇴직연금 계좌에서 리츠도 살 수 있어요?" 가 '운용방법 투자가능 한도 분류' 로 확장되어
# doc58 로 강제 배선됐고, 정작 df=23 짜리 정답어 '리츠'(기여 2.19)를 주입어 '운용방법'(기여 6.59)이
# 압도했다. 확장이 질의 자신의 낱말을 이기면 그건 검색이 아니라 배선이다.
#
# 남기는 기준 — **트리거 자체가 도메인 관용어**인 것만. 범용 한국어 동사구는 뺀다.
#   유지: 깨다(계좌를 깨다) · 옮기다/갈아타다(계좌 이전) · 떼다(세금) · 돌려받다(환급) · 위험자산
#   제거: 살 수 있 · 못 사 · 팔 수 있 · 담을 수 있 · 만들려 · 들어와 · 맘대로 · 어떤 상품
# 그리고 확장어는 EXPAND_WEIGHT 로 감쇠해 질의 자신의 낱말을 절대 넘지 못하게 한다.
EXPAND_WEIGHT = 0.35

CUSTOMER_TERM = [
    (r"못\s*사|살\s*수\s*없|사면\s*안", "운용방법 금지 레버리지 인버스"),
    (r"살\s*수\s*있|담을\s*수\s*있|편입\s*가능|넣을\s*수\s*있", "운용방법 투자가능 한도 분류"),
    (r"(상품|운용)\s*종류|어떤\s*상품|무슨\s*상품|상품\s*목록", "운용방법 분류 투자가능"),
    (r"위험자산", "위험자산 한도 투자한도"),
    (r"옮기|옮겨|가져오|가져올|갈아타|이사", "이전 실물이전 계약이전 이관"),
    (r"깨(면|고|서|는)|깨버리|해지하면", "해지 중도해지 연금외수령"),
    (r"맘대로|마음대로|임의로|멋대로", "동의 규약 절차 요건"),
    (r"돌려받|환급받", "세액공제 연말정산 환급"),
    (r"떼(요|가|나요|는)|얼마나\s*떼", "과세 원천징수 세율"),
    (r"들어와|들어오|나와요", "지급 지급기한"),
    (r"몇\s*시|언제까지\s*(주문|매수|매도)", "매매가능시간 정규장 영업일"),
    (r"팔\s*수\s*있|팔면|팔려", "매도 환매 중도해지"),
    (r"만들려|만들면|개설하려", "개설 신청 가입"),
]

def normalize(q: str) -> str:
    out = q
    for k, v in SYNONYM.items():
        if k in out.lower():
            out += " " + v
    for pat, v in ACRONYM.items():
        if re.search(pat, out, re.I):
            out += " " + v
    return out

# 질의에만 적용하는 기능어 제거. "어떻게 되나요" 같은 의문 표현이 만들어내는 n-gram 이
# 내용어 n-gram 과 같은 무게로 매칭되면 상위 후보가 잡음으로 채워진다.
QSTOP = re.compile(
    r"어떻게|어떤|어떠|무엇|뭐가|뭔지|뭔가요|얼마나|얼마|언제|어디|왜\s|되나요|인가요|하나요|합니까|"
    r"있나요|없나요|가능한가요|맞나요|할까요|건가요|것인가요|것들|알려\s*주?세요|설명해\s*주?세요|"
    r"정리해\s*주?세요|주세요|부탁드립니다|궁금합니다|해야\s*하나요|저는|제가|지금|요즘")

def ngrams(s: str, n: int = 2):
    s = re.sub(r"\s+", "", s)
    return [s[i:i+n] for i in range(len(s)-n+1)]

def tokens(text: str, use_ngram: bool = True, is_query: bool = False):
    t = normalize(text)
    if is_query:
        for pat, add in CUSTOMER_TERM:
            if re.search(pat, t): t += " " + add
        t = QSTOP.sub(" ", t)
    ws = [w.lower() for w in WORD.findall(t)]
    if not use_ngram:
        return ws
    grams = []
    for h in HANGUL.findall(re.sub(r"\s+", "", t)):
        if len(h) >= 2:
            grams += ngrams(h, 2)
            if len(h) >= 3: grams += ngrams(h, 3)
    return ws + grams


def _bag(t: str, use_ngram: bool = True):
    ws = [w.lower() for w in WORD.findall(t)]
    if not use_ngram:
        return ws
    grams = []
    for h in HANGUL.findall(re.sub(r"\s+", "", t)):
        if len(h) >= 2:
            grams += ngrams(h, 2)
            if len(h) >= 3: grams += ngrams(h, 3)
    return ws + grams


def query_terms(q: str):
    """질의를 (전체 토큰 Counter, 질의 자신의 어절, 확장으로만 생긴 토큰) 으로 쪼갠다.

    확장어에 낮은 가중치를 주려면 '무엇이 확장으로 생겼는지'를 알아야 한다.
    질의에 원래 있던 낱말이 확장어와 겹치면 그건 확장어로 치지 않는다(제 가중치 유지).
    """
    from collections import Counter
    base = normalize(q)
    add = ""
    for pat, a in CUSTOMER_TERM:
        if re.search(pat, base): add += " " + a
    base_q = QSTOP.sub(" ", base)
    add_q  = QSTOP.sub(" ", add)
    # n-gram 은 질의와 확장문을 **따로** 만든다. 이어붙인 뒤 공백을 지우면
    # "...오나요" + "이전..." 이 붙어 '나요이' 같은 어디에도 없는 조각이 생긴다.
    base_all = _bag(base_q)
    add_all  = _bag(add_q) if add_q.strip() else []
    own_words = set(_bag(base_q, use_ngram=False))
    expanded = set(add_all) - set(base_all)
    return Counter(base_all + add_all), own_words, expanded
