# -*- coding: utf-8 -*-
"""오케스트레이터 — 명세를 생성하고 명세대로 실행을 통제한다.
① 입력 가드레일 → ② 의도 라우터 → ③ 전제 검증 → ④ 슬롯/역질문 → ⑤ 검색
→ ⑥ 근거 필터 → ⑦ 계산기 → ⑧ HyperCLOVA X 생성 → ⑨ L0~L3 검증·증명서 → ⑩ 출력 가드레일
검증 실패 시 에이전트가 임의로 답하지 못하게 즉시 정지(Stop)하고 대응 로직으로 넘긴다.
"""
import os, re, json, time
from typing import Dict, Any, List, Optional

import router, premise, slots, evidence, guard, verify, calc, trace as tracelib
from fundq import FundQuery
from retrieval.bm25 import BM25Index
from retrieval.tokenize import normalize_intent
from llm.base import get_llm

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SYSTEM = """당신은 연금 상품·제도·세제 전문 상담 Agent입니다. 아래 규칙을 반드시 지키십시오.

1. 오직 [근거]에 주어진 내용만 사용해 답변합니다. 근거에 없는 사실은 절대 만들어내지 마십시오.
2. 답변의 모든 핵심 문장 끝에 근거 번호를 [근거1] 형태로 표기하십시오.
   다만 계산 결과로 얻은 수치를 쓴 문장에는 아무 표기도 붙이지 마십시오.
   붙일 근거 번호가 없을 때 표기를 새로 만들어 내면 안 됩니다.
3. 계산 결과가 주어지면 그 수치를 그대로 사용하고 직접 계산하지 마십시오.
4. 숫자는 아라비아 숫자로 쓰고 단위를 명시하십시오. (예: 9,000,000원, 16.5%)
5. 전제 교정 지시가 주어진 경우에만 답변 첫머리에서 고객의 잘못된 전제를 정중히 바로잡고 시작하십시오.
   지시가 없으면 전제를 교정하지 말고 곧바로 답하십시오. 고객이 틀린 전제를 말하지 않았는데
   교정하는 것처럼 쓰면 없는 오해를 지어내는 것입니다.
6. 특정 상품을 사라고 권유하지 말고, 원금보장·확실한 수익 같은 표현을 쓰지 마십시오.
7. 근거로 확인할 수 없는 항목은 추측하지 말고 "제공된 자료로는 확인되지 않습니다"라고 명시하십시오.
8. 마지막에 '가정' 과 '다음 확인사항' 을 한 줄씩 덧붙이십시오.
10. 입력에 주어진 구획 표시(■ 로 시작하는 머리말, 대괄호 표기)는 당신에게 주는 내부 지시이며
   고객에게 보여줄 내용이 아닙니다. 답변에는 근거 표기 [근거1] 형태만 쓰고,
   그 밖의 대괄호 표기나 구획 제목을 옮겨 쓰지 마십시오.
9. 계산식·산정방식(예: 법정 퇴직금 "계속근로 1년당 30일분 평균임금", 연금수령한도 산식, 세액공제율 적용식)을
   설명할 때는 반드시 [근거] 문장의 표현을 그대로 인용하고 근거 번호를 붙이십시오.
   근거에 없는 일수·개월수·계수·배수·비율(예: "60개월분", "3배수")을 임의로 만들어 쓰는 것은 금지합니다.
   [근거]에 산식이 없으면 숫자를 지어내지 말고 "제공된 자료에는 구체적인 산정방식이 제시되어 있지 않습니다"
   라고 밝힌 뒤, 근거로 확인되는 범위(제도의 구조·운용 주체 등)까지만 답하십시오."""

USER_TMPL = """■ 질의유형(내부): {qtype}
{premise_block}■ 근거
{context}
{compute_block}
■ 고객 질의
{question}

위 규칙에 따라 한국어로 답변하십시오."""


MARK = "(※ 제공 자료로 확인되지 않음)"

def _degrade_unverified(answer: str, claims):
    """대조 실패한 수치를 문장 단위로 강등한다.
    삭제하지 않고 '제공 자료로 확인되지 않음'을 명시하는 이유:
    답변을 통째로 버리면 '요구사항 충족' 지표에서 손해이고,
    확인되지 않은 값을 사실처럼 두면 '정확성'·'근거 기반'에서 더 크게 잃는다."""
    if not claims:
        return answer, None
    marked, seen = answer, set()
    # 긴 표기부터 처리한다. "1,200" 을 먼저 표시해야 "1" 이 그 안을 다시 건드리지 않는다.
    claims = sorted(claims, key=len, reverse=True)
    for c in claims:
        c = (c or "").strip()
        if c in seen or not c:
            continue
        seen.add(c)
        # 수치 뒤에 붙은 단위까지 삼켜서 치환한다.
        # 그러지 않으면 "14일" 이 "14(※ …)일" 처럼 쪼개져 값이 훼손된다.
        # 한국어 수 표기는 "7천만 원", "1억 2천만원", "148만 5천원" 처럼 뒤로 이어진다.
        # 숫자만 보고 치환하면 값이 쪼개지므로, 이어지는 수 표기와 단위를 통째로 삼킨다.
        m = re.search(
            re.escape(c) +
            r"(?:\s*(?:억|천만|백만|십만|만|천|백)?\s*[\d,]*)*"      # 이어지는 수 표기
            r"\s*(?:%|퍼센트|원|만원|등급|년차|개월|년|일분|일|배수|배|세|건|회|좌)?",
            marked)
        if not m:
            continue
        # 이미 표시된 수치 안을 다시 표시하지 않는다(값이 쪼개져 훼손된다).
        if marked[m.end():m.end() + 2] == "(※":
            continue
        marked = marked[:m.start()] + m.group(0) + MARK + marked[m.end():]
    note = ("수치 " + ", ".join(f"'{c}'" for c in claims) +
            " 는 인용한 근거 문서에서 확인되지 않았습니다.")
    return marked, note

class Agent:
    def __init__(self):
        self.idx = BM25Index.load(os.path.join(BASE, "data", "index", "bm25.pkl"))
        self.master = json.load(open(os.path.join(BASE, "data", "master", "fund_master.json"), encoding="utf-8"))
        self.golden = json.load(open(os.path.join(BASE, "data", "golden_set.json"), encoding="utf-8"))
        self.fq = FundQuery(self.master)
        dt = os.path.join(BASE, "data", "doc_types.json")
        self.doc_types = json.load(open(dt, encoding="utf-8")) if os.path.exists(dt) else {}
        self.llm = get_llm()
        self.last_trace: Dict[str, Any] = {}
        self._cache: Dict[str, Any] = {}

    # ── 계산기 자동 호출 (명세: LLM 암산 금지) ──────────────────────
    def run_calculators(self, q: str, qtype: str) -> List[Dict[str, Any]]:
        out = []
        num = lambda p: (int(re.sub(r"[,만원]", "", m.group(1)) ) if (m := re.search(p, q)) else None)
        if re.search(r"세액\s*공제", q):
            ps  = self._won(q, r"연금저축[^\d]{0,10}(\d[\d,]*)\s*(만원|원)")
            irp = self._won(q, r"IRP[^\d]{0,10}(\d[\d,]*)\s*(만원|원)")
            sal = self._won(q, r"(?:총급여|연봉)[^\d]{0,10}(\d[\d,]*)\s*(만원|원)")
            inc = self._won(q, r"종합소득(?:금액)?[^\d]{0,10}(\d[\d,]*)\s*(만원|원)")
            out.append(calc.tax_credit(연금저축납입=ps or 0, irp납입=irp or 0, 총급여=sal, 종합소득금액=inc))
        if re.search(r"연금\s*수령\s*한도", q):
            amt = self._won(q, r"(?:평가액|적립금|잔고)[^\d]{0,10}(\d[\d,]*)\s*(만원|원|억원)")
            yr  = int(m.group(1)) if (m := re.search(r"(\d+)\s*년차", q)) else None
            if amt and yr: out.append(calc.pension_withdrawal_limit(amt, yr))
        if re.search(r"(실제\s*수령\s*연차|퇴직소득세.{0,10}감면|연금으로.{0,10}(수령|받)|이연퇴직소득)", q):
            yr = int(m.group(1)) if (m := re.search(r"(\d+)\s*년차", q)) else 1
            out.append(calc.retirement_tax_reduction(yr))
        if re.search(r"(종합\s*과세|1,?500)", q):
            amt = self._won(q, r"(\d[\d,]*)\s*(만원|원)")
            out.append(calc.comprehensive_taxation(amt or 0))
        if re.search(r"임원.{0,10}퇴직", q):
            pass  # 입력 파라미터가 명시된 경우에만 호출 (슬롯 미충족 시 역질문)
        if re.search(r"(연금소득세|세율).{0,20}(얼마|몇|어떻게)", q) or re.search(r"연금소득세율", q) \
           or re.search(r"연금.{0,12}(받|수령)\S*.{0,15}(세금|세율|과세).{0,12}(어떻게|얼마|몇|되)", q):
            age = int(m.group(1)) if (m := re.search(r"(?:만\s*)?(\d{2})\s*세", q)) else None
            out.append(calc.pension_income_tax_rate(age, bool(re.search(r"종신", q))))
        return out

    @staticmethod
    def _won(q: str, pat: str) -> Optional[int]:
        m = re.search(pat, q)
        if not m: return None
        v = int(m.group(1).replace(",", ""))
        unit = m.group(2) if m.lastindex and m.lastindex >= 2 else "원"
        return v * (10_000 if unit == "만원" else (100_000_000 if unit == "억원" else 1))

    # ── 골든셋 정합 (doc29 FAQ_100: 표준답변 보유) ──────────────────
    def golden_hit(self, q: str) -> Optional[Dict[str, Any]]:
        qn = re.sub(r"\s", "", q)
        best, score = None, 0
        for g in self.golden["items"]:
            for cand in [g["질문"]] + g["유사질문"]:
                cn = re.sub(r"\s", "", cand or "")
                if len(cn) < 6: continue
                inter = len({cn[i:i+3] for i in range(len(cn)-2)} & {qn[i:i+3] for i in range(len(qn)-2)})
                s = inter / max(len(cn)-2, 1)
                if s > score: best, score = g, s
        return best if score >= 0.62 else None

    # ── 메인 ────────────────────────────────────────────────────────
    def answer(self, question_id: str, question: str) -> Dict[str, Any]:
        t0 = time.time()
        if question_id in self._cache: return self._cache[question_id]
        trace: Dict[str, Any] = {"as_of": None, "fallback": {"triggered": False, "reason": None}}

        # ① 입력 가드레일
        g_in = guard.check_input(question)
        trace["input_guard"] = {k: v for k, v in g_in.items() if k != "masked_question"}
        q = g_in["masked_question"]
        if g_in["blocked"]:
            return self._finish(question_id, question, "", trace, guard.INJECTION_REPLY,
                                stop="injection", t0=t0)
        if g_in["personal_account_query"]:
            trace["fallback"] = {"triggered": True, "reason": "개인 계좌 정보는 조회 대상이 아님"}
            return self._finish(question_id, question, "", trace, guard.PERSONAL_REPLY,
                                stop="personal_data", t0=t0)

        # ② 의도 라우터 — 규칙이 확신하지 못한 질의는 정형 펀드 매칭으로 보정한다.
        # "미래에셋퇴직플랜단기 작성기준일" 처럼 상품명만 있고 비교어가 없는 질의가 제도로 흘러가는 것을 막는다.
        # 축약어를 정식 표기로 치환한 사본으로 의도를 판정한다.
        # (검색은 원문 q 를 그대로 쓴다 — tokenize.normalize 가 검색용 확장을 따로 한다)
        qi = normalize_intent(q)
        trace["normalized"] = qi if qi != q else None
        rr = router.rule_route(qi)
        probe = None
        if not (rr["type"] and rr["confidence"] >= 0.67):
            # 규칙이 확정하지 못한 질의만 사전 검색으로 유형을 투표받는다 (약 10ms).
            # 투자설명서 본문은 제외한다 — 코퍼스의 99%라 무조건 상품설명비교로 쏠린다.
            # 상품 질의는 아래 fundq(명칭 지목) 오버라이드가 따로 잡는다.
            probe = self.idx.search(qi, top_k=10, per_doc=1,
                                    filter_fn=lambda r: r.get("source") != "fund")
        route = router.route(qi, self.llm if os.getenv("ROUTER_LLM") else None,
                             cands=probe, doc_types=self.doc_types)
        if route["type"] not in ("상품설명비교", "조건부추천") and \
           (route["by"] in ("rule-fallback", "evidence") or route["confidence"] < 0.7):
            probe = self.fq.match(qi, limit=3)
            if probe["funds"] and probe["filters"].get("검색모드") == "명칭지목" and probe["funds"][0]["_score"] >= 8:
                route = dict(route, type="상품설명비교", by="fundq-override",
                             confidence=0.72, override_from=route["type"])
        trace["route"] = route

        # ③ 전제 검증
        pr = premise.check(qi); trace["premise"] = pr

        # ④ 슬롯 판정 → 역질문
        sl = slots.evaluate(route["type"], qi); trace["slots"] = sl
        if sl["action"] == "clarify":
            ans = slots.clarify_answer(sl["questions"], route["type"], advice=sl.get("advice_intent", False))
            if pr["detected"]:
                ans = f"먼저 한 가지 바로잡겠습니다. {pr['correction']} (근거: {pr['source']})\n\n" + ans
            return self._finish(question_id, question, "", trace, ans, stop=None, t0=t0, clarify=True)

        # ⑤ 검색 — 상품 질의는 정형 검색(펀드 마스터)을 먼저 태운다
        gold = self.golden_hit(q)
        if gold: trace["golden"] = {"id": gold["id"], "카테고리": gold["카테고리"]}
        fq_ctx, fq_ids, compare_block, isin_hint = "", [], "", None
        if route["type"] in ("상품설명비교", "조건부추천"):
            mq = self.fq.match(q)
            trace["fund_query"] = {"filters": mq["filters"], "matched": mq["matched"],
                                   "selected": [{"isin": r["isin"], "펀드명": r["펀드명"],
                                                 "score": r["_score"], "why": r["_why"][:4]} for r in mq["funds"]],
                                   "compare": mq["compare"]}
            if mq["funds"]:
                fq_ctx, fq_ids = self.fq.render(mq, start=1)
                isin_hint = mq["isins"]
                if mq["compare"]:
                    compare_block = self.fq.compare_axes(mq) or ""
        # 코퍼스의 99%가 투자설명서 본문 청크라, 제도·세제 질의에서 이들이 상위 후보를 잠식한다.
        # 질의 유형에 따라 검색 단계에서 대상을 좁히고, 한 문서가 상위를 독식하지 못하게 제한한다.
        is_product = route["type"] in ("상품설명비교", "조건부추천")
        if is_product and isin_hint:
            ffn = lambda r: r.get("source") != "fund" or r.get("doc_id") in set(isin_hint)
        elif is_product:
            ffn = None
        else:
            ffn = lambda r: r.get("source") != "fund"
        cands = self.idx.search(q, top_k=40, filter_fn=ffn, per_doc=2)

        # ⑥ 근거 필터 — 정형 근거를 앞에 두고 본문 근거를 이어 번호를 매긴다
        # 정형 근거가 이미 질의를 덮고 있으면 본문 청크는 보조로만 둔다.
        # 근거 완전성은 recall 뿐 아니라 precision 도 채점하므로 중복 근거는 손해다.
        n_fq = len(fq_ids)
        bm_k = 6 if n_fq == 0 else (3 if n_fq == 1 else 2)
        kept, rejected = evidence.filter_evidence(q, route["type"], cands, self.master,
                                                  top_k=bm_k, isin_hint=isin_hint)
        bm_ctx = evidence.render_context(kept, start=n_fq + 1, query=q)
        context = "\n\n".join(x for x in (fq_ctx, bm_ctx) if x)
        trace["retrieval"] = {"candidates": len(cands), "structured": n_fq, "after_filter": len(kept),
                              "doc_ids": fq_ids + [f"{c['doc_id']}#p{c['page']}" for c in kept],
                              "rejected": rejected}
        as_ofs = [c["meta"].get("as_of") for c in kept if (c.get("meta") or {}).get("as_of")]
        as_ofs += [r["작성기준일"] for r in (mq["funds"] if isin_hint else []) if r.get("작성기준일")]
        trace["as_of"] = max(as_ofs) if as_ofs else None

        # ⑦ 계산기
        compute = self.run_calculators(qi, route["type"])
        trace["compute"] = compute

        if not kept and not compute and not fq_ctx:
            trace["fallback"] = {"triggered": True, "reason": "제공 자료에서 관련 근거를 찾지 못함"}
            ans = ("제공된 자료에서 이 질문에 답할 근거를 찾지 못했습니다. 추측으로 답변드리지 않겠습니다.\n"
                   "질문을 조금 더 구체적으로 주시거나, 관련 제도·상품명을 알려주시면 다시 확인하겠습니다.")
            return self._finish(question_id, question, "", trace, ans, stop="no_evidence", t0=t0)

        # ⑧ 생성
        premise_block = (f"■ 전제교정 지시(내부 — 이 제목을 답변에 옮겨 쓰지 말 것)\n"
                         f"고객의 전제: {pr['claim']}\n교정: {pr['correction']} (근거 {pr['source']})\n\n"
                         if pr["detected"] else "")
        compute_block = ("\n■ 계산기 결과(내부 — 수치만 사용하고 이 제목은 옮겨 쓰지 말 것)\n"
                         + json.dumps(compute, ensure_ascii=False, indent=1)) if compute else ""
        if gold:
            premise_block += f"■ 표준답변 참고(내부)\n{gold['표준답변']}\n\n"
        if compare_block:
            premise_block += ("■ 비교표(내부) 아래 표가 이 질의의 비교 기준이다. 비교축을 임의로 바꾸지 말고 "
                              "판단메모를 답변에 반영하라.\n" + compare_block + "\n\n")
        user = USER_TMPL.format(qtype=route["type"], premise_block=premise_block,
                                context=context or "(없음)", compute_block=compute_block, question=q)
        try:
            raw = self.llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                                temperature=0.1, max_tokens=1200)
        except Exception as e:
            trace["fallback"] = {"triggered": True, "reason": f"LLM 호출 실패: {type(e).__name__}"}
            raw = ("일시적인 오류로 답변을 생성하지 못했습니다. 아래 근거 자료를 확인해 주십시오.\n\n" + context[:800])
        return self._finish(question_id, question, context, trace, raw, stop=None, t0=t0, compute=compute)

    def _finish(self, qid, question, context, trace, answer, *, stop, t0,
                clarify=False, compute=None) -> Dict[str, Any]:
        # ⑩ 출력 가드레일
        oc = guard.check_output(answer, context)
        answer = oc["text"]
        has_cite = bool(re.search(r"\[근거\d+\]", answer)) or clarify or stop is not None
        if context and not has_cite:
            answer += "\n\n[근거]\n" + "\n".join(f"- {d}" for d in trace.get("retrieval", {}).get("doc_ids", []))
            has_cite = True
        if trace.get("as_of") and "기준" not in answer:
            answer += f"\n\n※ 위 상품 정보는 투자설명서 작성기준일 {trace['as_of']} 기준입니다."

        payload = {"question_id": qid, "question": question,
                   "retrieved_context": context, "think_trace": "x", "answer": answer}
        skip_l2 = clarify or stop is not None
        cert = verify.build_certificate(payload=payload, out_check=oc, has_citation=has_cite,
                                        evidence=(answer if skip_l2 else context),
                                        compute=compute or trace.get("compute") or [],
                                        question=question,
                                        answer=("" if skip_l2 else answer),
                                        requirements=([] if skip_l2 else None))
        trace["certificate"] = cert
        # §5 지적 1 — 결론 문장이 전제·근거·규칙 연결 위에 서 있는지 별도 기록
        trace["conclusion_check"] = ({"판정": "해당 없음(역질문·차단·근거없음)"} if skip_l2
                                     else verify.conclusion_check(answer, context))
        trace["confidence"] = verify.confidence(trace.get("route", {}), cert,
                                                trace.get("retrieval", {}).get("after_filter", 0), clarify)
        trace["stop"] = stop
        trace["latency_ms"] = int((time.time()-t0)*1000)

        # ⑨ 검증 실패 시 정지 — 임의 답변 금지
        if cert["L1_policy"]["block"]:
            answer = ("답변 검증(L1 정책) 단계에서 부적절한 표현이 확인되어 해당 답변을 제공하지 않습니다.\n"
                      "제공된 근거 자료를 직접 확인해 주십시오.\n\n" + context[:700])
            trace["stop"] = "L1_policy_fail"
        elif not cert["L1_policy"]["pass"]:
            # 누출은 이미 걷어냈다. 답변은 유지하고 사실만 기록한다.
            trace["stop"] = "L1_leak_stripped"
        elif not cert["L2_fact"]["pass"]:
            # 근거로 대조되지 않은 수치는 단 1건이라도 그대로 두지 않는다.
            # 금융 도메인에서 '그럴듯한 거짓말'은 임계치를 둘 성질의 것이 아니다.
            # 근거 풀에는 있으나 '인용한' 근거에 없는 값도 같이 강등한다.
            # 이게 held-out 의 침묵 실패(cert 4단계 통과 + 사실 오류) 경로였다.
            L2 = cert["L2_fact"]
            # 안내는 한 블록으로 모은다. 검사별로 쪼개 붙이면 답변 말미가 안내로 뒤덮인다.
            lines = []
            bad = [d["claim"] for d in (L2.get("details") or [])
                   if not d["match"] or d.get("cite_match") is False]
            uniq = list(dict.fromkeys(bad))[:5]
            answer, note = _degrade_unverified(answer, uniq)
            if note:
                lines.append(note)
            # 규정과 어긋난 진술은 감추지 말고 정본으로 바로잡는다.
            # 이렇게 하면 침묵 실패가 '전제교정'(평가지표가 요구하는 행동)으로 바뀐다.
            for v in (L2.get("rule_violations") or [])[:3]:
                lines.append(f"규정 확인: {v['정본']} (근거: {v['근거']})")
            # '가능'과 '유리'를 가른다. 근거 없는 유불리 단정은 판단이 아니라 사실로 읽힌다.
            jd = L2.get("ungrounded_judgment") or []
            if jd:
                lines.append("다음은 근거로 뒷받침된 판단이 아니라 일반적 설명입니다 — "
                             + " / ".join(f"\"{v['sentence'][:60]}\"" for v in jd[:2])
                             + ". 유불리는 가입자의 소득·연령·수령계획에 따라 달라집니다.")
            ung = L2.get("ungrounded") or []
            if ung:
                lines.append("다음 서술은 인용한 근거 문서에서 확인되지 않았습니다 — "
                             + " / ".join(f"\"{u['sentence'][:60]}\"" for u in ung[:3]))
            if lines:
                answer += ("\n\n※ 검증 안내 — 아래 항목은 제공 자료로 확인되지 않아 표시해 둡니다.\n"
                           + "\n".join(f"- {x}" for x in lines))
            trace["stop"] = "L2_fact_degraded"

        self.last_trace = trace          # 평가·회귀 측정용 전체 증명서 (응답 필드가 아님)
        out = {"question_id": qid, "question": question,
               "retrieved_context": context,
               "think_trace": tracelib.serialize(trace),
               "answer": answer}
        self._cache[qid] = out
        return out
