# -*- coding: utf-8 -*-
"""슬롯 충족도 판정 → 미충족 시 역질문(Clarify)으로 종료.
설명회 테크세션: FM-2.2 'Fail to Ask for Clarification'은 금융권에서 치명적.
과제 PDF 참고질의 5번("좋은 연금 상품 하나 추천해 주세요")은 답하면 감점, 역질문해야 정답.
"""
import re
from typing import Dict, Any, List

SPEC = {
  "조건부추천": {
     "required": ["계좌유형", "투자기간", "위험감내"],
     "detect": {
        "계좌유형": r"(IRP|개인형퇴직연금|연금저축|DC|DB|ISA|퇴직연금)",
        "투자기간": r"(\d+\s*년|\d+\s*개월|장기|단기|중장기|은퇴까지|만기)",
        "위험감내": r"(안정|안전|보수적|공격적|적극적|중립|균형|원금|손실.{0,6}(감내|감수|괜찮|봐도)|위험.{0,4}(선호|회피)|중위험|고위험|저위험)",
     },
     "questions": {
        "계좌유형": "어떤 계좌에 담으실 계획인가요? (IRP / 연금저축 / DC / ISA)",
        "투자기간": "연금 수령 시작까지 예상 투자기간이 어느 정도인가요?",
        "위험감내": "원금 손실 가능성을 어느 정도까지 감내하실 수 있나요? (안정형 / 중립형 / 적극형)",
     }},
  "세제": {
     "required": [], "detect": {}, "questions": {},
     "conditional": {
        # 한도(600/900만원)는 소득과 무관한 확정값 → 직답.
        # '실제로 얼마를 돌려받는가'(공제율 16.5/13.2% 적용) 만 소득구간이 필요하다.
        # 과잉 역질문은 '요구사항 충족' 지표에서 실점하므로 트리거를 좁게 유지한다.
        "소득구간": (r"(세액\s*공제|연말정산).{0,25}(돌려받|환급|절세액|얼마나?\s*줄|얼마나?\s*아[낄껴]|세금.{0,4}얼마)",
                   r"(총급여|연봉|종합소득|소득).{0,10}\d",
                   "세액공제율은 총급여 5,500만원(종합소득금액 4,500만원) 기준으로 16.5%와 13.2%로 갈립니다. 해당 구간을 알려주시면 정확한 금액을 산출해 드립니다."),
     }},
}

# ── 라우터 무관 추천 의도 탐지기 ──────────────────────────────────────────
# MAST FM-2.2. 안전 동작(역질문)을 분류기 하나에 매달면 분류기가 틀리는 순간 무방비가 된다.
# 벤치 BH-42/45/46/48/49 는 라우터가 조건부추천을 놓쳐 슬롯 게이트를 통째로 우회했다.
# 따라서 "추천·선택을 요구하는 신호"는 라우트와 무관하게 직접 본다.
# 표면 어구를 나열하면 어순이 조금만 달라져도 게이트가 무너진다(동형문항 B-183에서 확인).
# "선택 대상 명사"와 "적합/선택 요구 술어"를 한 절(clause) 안에서 잇는 구조로 본다.
_CL = r"[^?？.!\n]{0,14}"          # 절 경계를 넘지 않는 짧은 간격 (공백 허용)
_TGT = r"(상품|펀드|유형|종목|계좌|것|게|걸|거)"
_ASK = r"(좋|맞|나을|괜찮|골라|고르|봐야|사야|추천|할까|담|넣)"
ADVICE_INTENT = re.compile("|".join([
    r"추천",
    r"(골라|고르)\s*(줘|주|야|는)",
    r"(어떤|어느|무슨)\s*" + _TGT + _CL + _ASK,
    r"(뭐|뭘|무엇)(을|를)?" + _CL + r"(좋|맞|봐야|고르|골라)",
    r"(뭐|뭘|무엇)(을|를)?" + _CL + r"(사|살|담|넣)(면|야|까|는|지|아)",
    r"(저|제|내|나)\s*한테\s*(맞|좋)",
    r"(담|넣|사|골라)\S{0,3}면\s*좋",
    r"어떻게\s*(구성|굴리|운용하)",
    r"(제일|가장)\s*(유리|좋|나은)",
]))

def wants_advice(q: str) -> bool:
    """상품 선택·추천을 요구하는 질의인가. 라우트를 보지 않는다."""
    return bool(ADVICE_INTENT.search(q or ""))

def _apply(spec, q):
    filled, missing, qs = [], [], []
    for k in spec["required"]:
        (filled if re.search(spec["detect"][k], q, re.I) else missing).append(k)
    qs += [spec["questions"][k] for k in missing]
    for name, (trigger, present, msg) in (spec.get("conditional") or {}).items():
        if re.search(trigger, q) and not re.search(present, q):
            missing.append(name); qs.append(msg)
    return filled, missing, qs

def evaluate(qtype: str, q: str) -> Dict[str, Any]:
    """qtype 의 슬롯 명세를 적용하되, 추천 요구가 감지되면 라우트와 무관하게
    조건부추천 명세를 '추가로' 적용한다(원 명세의 조건부 항목도 잃지 않는다)."""
    specs, forced = [], False
    if qtype in SPEC: specs.append((qtype, SPEC[qtype]))
    if qtype != "조건부추천" and wants_advice(q):
        specs.append(("조건부추천", SPEC["조건부추천"])); forced = True
    if not specs:
        return {"required": [], "filled": [], "missing": [], "action": "answer",
                "questions": [], "advice_intent": False, "gate": None}
    required, filled, missing, qs = [], [], [], []
    for _, spec in specs:
        f, m, q_ = _apply(spec, q)
        required += [x for x in spec["required"] if x not in required]
        filled += [x for x in f if x not in filled]
        for x, y in zip(m, q_):
            if x not in missing: missing.append(x); qs.append(y)
    action = "clarify" if missing else "answer"
    return {"required": required, "filled": filled, "missing": missing,
            "action": action, "questions": qs,
            "advice_intent": wants_advice(q),
            "gate": "route-independent" if forced else "route"}

def clarify_answer(qs: List[str], qtype: str, advice: bool = False) -> str:
    head = ("정확한 답변을 위해 먼저 확인이 필요합니다. 아래 항목을 알려주시면 상황별로 정리해 드리겠습니다.\n\n"
            if (qtype == "조건부추천" or advice) else "정확한 산출을 위해 아래 항목 확인이 필요합니다.\n\n")
    body = "\n".join(f"{i}. {x}" for i, x in enumerate(qs, 1))
    tail = ("\n\n※ 특정 상품을 단정적으로 추천드리지 않는 이유는, 같은 상품이라도 계좌 유형·투자기간·위험감내 수준에 따라 "
            "적합 여부가 달라지기 때문입니다.") if (qtype == "조건부추천" or advice) else ""
    return head + body + tail
