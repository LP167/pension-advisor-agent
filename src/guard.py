# -*- coding: utf-8 -*-
"""입출력 가드레일. 설명회 테크세션 ①Rule 처리(안전/비용 관문) + ⑩출력 필터링."""
import os, re
from typing import Tuple, Dict, Any, List

PII = [
    (r"\d{6}[-–]\s?[1-4]\d{6}", "[주민등록번호]"),
    (r"\b\d{2,3}-\d{3,4}-\d{4}\b", "[전화번호]"),
    (r"\b[\w.+-]+@[\w-]+\.[\w.]+\b", "[이메일]"),
    (r"\b\d{3}-\d{2}-\d{6}\b", "[계좌번호]"),
    (r"\b\d{10,16}\b", "[번호]"),
]
INJECTION = [
    r"(이전|위|앞)의?\s*(지시|명령|규칙|프롬프트).{0,10}(무시|잊)",
    r"ignore\s+(all\s+)?(previous|above)", r"system\s*prompt", r"프롬프트를?\s*(출력|보여|알려)",
    r"너의?\s*(지시문|설정|규칙).{0,8}(알려|출력|보여)", r"개발자\s*모드", r"jailbreak",
    r"역할을?\s*(무시|바꿔)", r"제한\s*없이\s*답변",
    r"(당신|너|니)의?\s*(규칙|지침|정책|가이드라인|제약).{0,10}(무시|어기|벗어|풀)",
    r"규칙을?\s*(무시|어기|어겨)", r"(라고|이라고)\s*말해\s*(줘|주세요)",
]
# 정보제공 vs 투자권유 — 금융소비자보호 관점
ADVICE_BAN = [
    (r"(반드시|무조건|100%|확실히)\s*(수익|오릅|이득|유리)", "단정적 수익 표현"),
    (r"원금\s*(이\s*)?보장", "원금보장 표현"),
    (r"(이|그)\s*(상품|펀드)를?\s*(사세요|매수하세요|가입하세요)", "단정적 투자권유"),
    (r"손실\s*(이\s*)?(없|나지\s*않)", "손실 부인"),
]
# 개인 계좌 '조회 요청'만 차단한다. 지시대명사만 보고 막으면
# "공시 수익률과 제 계좌 수익률이 왜 다른가요" 같은 방법론 질의까지 잃는다(벤치 BG-30).
# 판정: (개인 계좌를 가리키는 표현) AND (조회 의도 동사) — 둘 다 있을 때만 차단.
PERSONAL_SUBJ = [r"(내|제|본인|나의)\s*(계좌|IRP|연금저축|연금계좌|퇴직연금|적립금)",
                 r"(내|제)\s*(잔고|수익률|보유|평가액|적립금)"]
PERSONAL_OBJ  = [r"잔고", r"보유\s*(수량|평가액|내역|종목)", r"계좌\s*번호", r"평가\s*금액"]
LOOKUP_VERB   = [r"알려", r"조회", r"보여\s*(줘|주)", r"확인해\s*(줘|주)", r"말해\s*(줘|주)",
                 r"얼마", r"몇\s*(주|원|개)", r"궁금", r"뽑아\s*(줘|주)"]
# 하위호환: 기존 이름을 참조하는 코드가 있어도 깨지지 않게 남긴다
PERSONAL_DATA = PERSONAL_SUBJ + PERSONAL_OBJ

def _personal_hits(q: str):
    subj = [p for p in PERSONAL_SUBJ + PERSONAL_OBJ if re.search(p, q)]
    verb = [p for p in LOOKUP_VERB if re.search(p, q)]
    return (subj + verb) if (subj and verb) else []
# 개인 식별정보가 실린 질의는 그 자체로 개인정보 조회 요청으로 본다
PII_AS_PERSONAL = (r"\[주민등록번호\]", r"\[계좌번호\]")

def mask_pii(t: str):
    hits = 0
    for pat, rep in PII:
        t, n = re.subn(pat, rep, t); hits += n
    return t, hits

def check_input(q: str) -> Dict[str, Any]:
    masked, n = mask_pii(q)
    inj = [p for p in INJECTION if re.search(p, q, re.I)]
    personal = _personal_hits(q) + [p for p in PII_AS_PERSONAL if re.search(p, masked)]
    return {"masked_question": masked, "pii_masked": n,
            "injection_detected": bool(inj), "injection_hits": len(inj),
            "personal_account_query": bool(personal),
            "blocked": bool(inj)}

INJECTION_REPLY = ("요청하신 내용은 시스템 설정이나 내부 지시문에 관한 것으로 확인되어 답변드릴 수 없습니다. "
                   "연금 제도·세제·상품에 관한 질문을 주시면 제공된 자료를 근거로 답변드리겠습니다.")
PERSONAL_REPLY = ("개별 고객의 계좌 잔고·보유내역·수익률은 이 시스템이 조회할 수 있는 정보가 아닙니다. "
                  "해당 정보는 거래하시는 금융회사의 앱 또는 고객센터에서 확인하실 수 있습니다.\n"
                  "제도·세제·상품 일반에 관한 질문은 제공된 자료를 근거로 답변드릴 수 있습니다.")

# 프롬프트 골격 누출 — 내부 지시 구획이 고객 답변에 그대로 실려 나가는 것.
# 시스템의 내부 구조를 노출할 뿐 아니라, 전제교정 지시가 없는데도 "[전제교정]" 머리말을
# 지어 붙이면 고객이 하지도 않은 오해를 교정한 것처럼 보인다(환각성 전제교정).
PROMPT_LEAK = [
    (r"\[\s*전제\s*교정\s*\]", "프롬프트 골격 누출(전제교정)"),
    (r"■\s*전제교정", "프롬프트 골격 누출(전제교정)"),
    (r"\[\s*계산기\s*\]", "프롬프트 골격 누출(계산기)"),
    (r"\[\s*질의\s*유형\s*\]", "프롬프트 골격 누출(질의유형)"),
    (r"\[\s*비교표\s*\]", "프롬프트 골격 누출(비교표)"),
    (r"\[\s*표준답변\s*참고\s*\]", "프롬프트 골격 누출(표준답변)"),
    (r"\[\s*질의\s*\]", "프롬프트 골격 누출(질의)"),
    (r"\[\s*답변\s*\]", "프롬프트 골격 누출(답변)"),
    (r"■\s*(근거|계산기|고객\s*질의|표준답변|비교표)", "프롬프트 골격 누출(구획머리말)"),
]
# 파이프라인이 근거 미표기 답변에 직접 덧붙이는 블록은 누출이 아니다.
_SELF_CITE = re.compile(r"\n\n\[근거\]\n(?:- \S+\n?)+")

# 개별 리터럴을 나열하면 모델이 표기를 조금 바꿔 빠져나간다("[계산기]" → "[계산기 결과]").
# 대괄호 토큰 중 정상 인용([근거n])이 아니면서 내부 구획 어휘를 담은 것을 통째로 잡는다.
_BRACKET = re.compile(r"\[([^\]\n]{1,24})\]")
_CITE_OK = re.compile(r"^근거\s*\d+$")
_SCAFFOLD = [(r"전제\s*교정", "전제교정"), (r"계산기", "계산기"), (r"질의\s*유형", "질의유형"),
             (r"비교표", "비교표"), (r"표준\s*답변", "표준답변"), (r"내부", "내부지시"),
             (r"^질의$", "질의"), (r"^답변$", "답변"), (r"^근거$", "근거머리말")]

def find_leaks(ans: str):
    probe = _SELF_CITE.sub("", ans or "")
    seen = []
    for m in _BRACKET.finditer(probe):
        inner = m.group(1).strip()
        if _CITE_OK.match(inner):
            continue                                  # [근거3] 등 정상 인용
        for pat, name in _SCAFFOLD:
            if re.search(pat, inner):
                lbl = f"프롬프트 골격 누출({name})"
                if lbl not in seen: seen.append(lbl)
                break
    for pat, name in PROMPT_LEAK:                     # ■ 구획 머리말
        if pat.startswith("■") and re.search(pat, probe):
            if name not in seen: seen.append(name)
    return seen

def strip_leaks(ans: str) -> str:
    """누출된 구획 표시를 제거한다. 본문은 남기되 골격만 걷어낸다."""
    def _sub(m):
        inner = m.group(1).strip()
        if _CITE_OK.match(inner): return m.group(0)
        return "" if any(re.search(p, inner) for p, _ in _SCAFFOLD) else m.group(0)
    out = _BRACKET.sub(_sub, ans or "")
    out = re.sub(r"■\s*(전제교정|근거|계산기|고객\s*질의|표준답변|비교표)[^\n]*\n?", "", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()

# ── 출력 위생 (파괴 테스트 §6-5 에서 발견) ──────────────────────────────
# 실측: LLM 이 200,000자를 반환하면 그대로 응답에 실렸고, 제어문자·ANSI 이스케이프도
# 답변에 그대로 남았다. max_tokens 는 '요청' 파라미터라 응답 길이를 보장하지 않는다
# (게이트웨이 오작동·폴백 모델에서 깨질 수 있다).
# 09.06 동결 후에는 손댈 수 없으므로 출력단에서 잘라 둔다.
MAX_ANSWER_CHARS = int(os.getenv("MAX_ANSWER_CHARS", "8000"))
# \n \t 만 남기고 제어문자·ANSI 이스케이프 제거
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

def sanitize_output(t: str) -> Tuple[str, Dict[str, Any]]:
    """채점 시스템이 받아 파싱할 문자열이다. 깨질 수 있는 것을 남기지 않는다."""
    info = {"ansi_stripped": 0, "ctrl_stripped": 0, "truncated_from": 0}
    t = t or ""
    t, n1 = _ANSI.subn("", t);  info["ansi_stripped"] = n1
    t, n2 = _CTRL.subn("", t);  info["ctrl_stripped"] = n2
    if len(t) > MAX_ANSWER_CHARS:
        info["truncated_from"] = len(t)
        t = (t[:MAX_ANSWER_CHARS].rstrip()
             + f"\n\n※ 응답 길이 상한({MAX_ANSWER_CHARS:,}자)에 도달해 이후를 생략했습니다.")
    return t, info


def check_output(ans: str, evidence_text: str) -> Dict[str, Any]:
    ans, hygiene = sanitize_output(ans)
    viol = [name for pat, name in ADVICE_BAN if re.search(pat, ans)]
    leaks = find_leaks(ans)
    masked, n = mask_pii(ans)
    if leaks:
        masked = strip_leaks(masked)      # 고객에게는 어떤 경우에도 골격이 나가지 않는다
    return {"advice_violation": len(viol), "violations": viol + leaks,
            "leak_violation": len(leaks), "leaks": leaks,
            "pii_masked": n, "text": masked, "hygiene": hygiene}
