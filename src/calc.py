# -*- coding: utf-8 -*-
"""결정론적 세제 계산기.
LLM은 숫자를 계산하지 않는다. 모든 수치는 여기서 산출되고 rule_source(제공자료)로 근거를 남긴다.
근거: 제공 자료가 최종 근거이며 상충 시 제공자료 우선(과제 규정).
"""
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Any

MAN = 10_000  # 만원

def _r(fn, inputs, output, rule_source, note=None):
    return {"fn": fn, "input": inputs, "output": output, "rule_source": rule_source, "note": note}

# ── 1. 세액공제 한도 (doc41) ────────────────────────────────────────────
LIMIT_PENSION_SAVING = 600 * MAN     # 연금저축 단독 세액공제 한도
LIMIT_TOTAL          = 900 * MAN     # 연금저축 + IRP 합산 세액공제 한도
LIMIT_DEPOSIT        = 1_800 * MAN   # 연금저축 + IRP 합산 납입한도
RATE_HIGH, RATE_LOW  = 0.165, 0.132
THRESH_SALARY, THRESH_INCOME = 5_500 * MAN, 4_500 * MAN

def tax_credit(연금저축납입: int = 0, irp납입: int = 0,
               총급여: Optional[int] = None, 종합소득금액: Optional[int] = None) -> Dict[str, Any]:
    ps  = min(연금저축납입, LIMIT_PENSION_SAVING)
    tot = min(ps + irp납입, LIMIT_TOTAL)
    if 총급여 is not None:        rate = RATE_HIGH if 총급여 <= THRESH_SALARY else RATE_LOW
    elif 종합소득금액 is not None: rate = RATE_HIGH if 종합소득금액 <= THRESH_INCOME else RATE_LOW
    else:                         rate = None
    out = {"공제대상_연금저축": ps, "공제대상_합산": tot,
           "세액공제율": rate, "예상세액공제액": int(tot*rate) if rate else None,
           "납입한도_합산": LIMIT_DEPOSIT,
           "최대절세액_이론치": int(LIMIT_TOTAL*RATE_HIGH)}
    if rate is None:
        out["한계고지"] = "총급여 또는 종합소득금액이 없으면 세액공제율(16.5%/13.2%)을 확정할 수 없습니다."
    out["유의"] = "실제 공제액은 산출세액을 한도로 하므로, 납부할 세금이 적으면 그만큼 줄어듭니다."
    return _r("tax_credit", {"연금저축납입":연금저축납입,"irp납입":irp납입,
                             "총급여":총급여,"종합소득금액":종합소득금액}, out,
              "doc41 연금저축계좌·IRP 세액공제 안내")

# ── 2. 연금수령한도 (doc39) ─────────────────────────────────────────────
def pension_withdrawal_limit(평가액: int, 연금수령연차: int) -> Dict[str, Any]:
    if 연금수령연차 >= 11:
        out = {"한도": None, "무제한": True,
               "설명": "연금수령연차 11년차부터는 한도가 없어 전액 인출도 연금수령으로 인정됩니다."}
    else:
        limit = int(평가액 / (11 - 연금수령연차) * 1.2)
        out = {"한도": limit, "무제한": False,
               "계산식": f"{평가액:,} ÷ (11 - {연금수령연차}) × 120% = {limit:,}원"}
    return _r("pension_withdrawal_limit", {"평가액":평가액,"연금수령연차":연금수령연차}, out,
              "doc39 연금수령한도 안내")

def 연금수령연차_기산(가입일_2013_03_01_이전: bool, dc_db_전액이체: bool = False) -> Dict[str, Any]:
    base = 6 if (가입일_2013_03_01_이전 or dc_db_전액이체) else 1
    return _r("연금수령연차_기산", {"2013.3.1이전가입":가입일_2013_03_01_이전,"DC/DB전액이체":dc_db_전액이체},
              {"기산연차": base,
               "설명": "2013.3.1 이전 가입 연금계좌는 6년차부터 기산합니다. 2013.3.1 이전 DC/DB 가입자가 퇴직금 전액을 신규 연금계좌로 이체하는 경우에도 6년차 특례가 적용됩니다."},
              "doc39 연금수령한도 안내 — 연금수령연차 6년차 특례")

def 연금수령요건(가입기간_년: Optional[float], 만나이: Optional[int],
              퇴직금재원있음: bool = False) -> Dict[str, Any]:
    미확인 = [k for k, v in {"가입기간": 가입기간_년, "만나이": 만나이}.items() if v is None]
    조건 = {
        "가입기간_5년이상": None if 가입기간_년 is None else (퇴직금재원있음 or 가입기간_년 >= 5),
        "만55세이상": None if 만나이 is None else 만나이 >= 55,
        "연금수령한도_이내": "인출계획 필요",
    }
    return _r("연금수령요건", {"가입기간_년":가입기간_년,"만나이":만나이,"퇴직금재원있음":퇴직금재원있음},
              {"조건": 조건, "미확인": 미확인,
               "설명": "연금계좌 가입기간 5년 이상 + 만 55세 이후 + 연금수령한도 이내 인출. 퇴직금이 있으면 가입기간 요건은 적용되지 않습니다."},
              "doc39 연금수령한도 안내")

# ── 3. 이연퇴직소득세 감면 (doc40) ──────────────────────────────────────
def retirement_tax_reduction(연금실제수령연차: int) -> Dict[str, Any]:
    rate = 0.30 if 연금실제수령연차 < 11 else (0.40 if 연금실제수령연차 < 21 else 0.50)
    return _r("retirement_tax_reduction", {"연금실제수령연차":연금실제수령연차},
              {"감면율": rate,
               "적용대상": "이연퇴직소득에 한함",
               "설명": "퇴직금을 연금으로 수령하면 퇴직소득세의 30%가 감면되고, 연금실제수령연차 11년차부터 40%, 21년차부터 50%로 확대됩니다.",
               "주의": "연금수령연차(한도 결정)와 연금실제수령연차(감면율 결정)는 다른 개념입니다. 실제로 인출한 연도만 누적됩니다."},
              "doc40 연금실제수령연차 안내")

# ── 4. 사적연금 종합과세 (doc38 / doc39 / doc20) ────────────────────────
THRESH_COMPREHENSIVE = 1_500 * MAN
def pension_income_tax_rate(만나이: Optional[int], 종신연금: bool = False) -> Dict[str, Any]:
    if 종신연금 and 만나이 is not None and 55 <= 만나이 < 70: rate = 0.044
    elif 만나이 is None: rate = None
    elif 만나이 < 70:  rate = 0.055
    elif 만나이 < 80:  rate = 0.044
    else:              rate = 0.033
    return _r("pension_income_tax_rate", {"만나이":만나이,"종신연금":종신연금},
              {"연금소득세율": rate,
               "구간": "55~69세 5.5% / 70~79세 4.4% / 80세 이상 3.3% (종신연금 수령 시 55세 이상 70세 미만 4.4%)",
               "한계고지": None if rate else "연령 정보가 없으면 세율을 확정할 수 없습니다."},
              "doc38 연금소득 종합과세 안내 / doc39 / doc20")

def comprehensive_taxation(과세대상_사적연금소득: int) -> Dict[str, Any]:
    over = 과세대상_사적연금소득 > THRESH_COMPREHENSIVE
    return _r("comprehensive_taxation", {"과세대상_사적연금소득":과세대상_사적연금소득},
              {"기준금액": THRESH_COMPREHENSIVE, "초과여부": over,
               "결과": ("전액에 대해 종합과세 또는 16.5% 분리과세를 선택합니다. 16.5%는 초과분이 아니라 전체금액에 적용됩니다."
                        if over else "연령별 3.3~5.5%로 분리과세되어 타 소득과 합산하지 않습니다."),
               "판정대상_제외": "세액공제를 받지 않은 납입액과 퇴직금 재원은 1,500만원 판정에 포함되지 않습니다.",
               "종합과세세율범위": "6.6% ~ 49.5% (지방소득세 포함)"},
              "doc38 연금소득 종합과세 안내")

# ── 5. 임원 퇴직소득 한도 (doc45) ───────────────────────────────────────
def executive_severance_limit(퇴직전3년_연평균총급여: int, 근속연수_2020년이후: float,
                              근속연수_2012_2019: float = 0.0) -> Dict[str, Any]:
    a = 퇴직전3년_연평균총급여 / 10 * 3 * 근속연수_2012_2019
    b = 퇴직전3년_연평균총급여 / 10 * 2 * 근속연수_2020년이후
    total = int(a + b)
    return _r("executive_severance_limit",
              {"퇴직전3년_연평균총급여":퇴직전3년_연평균총급여,
               "근속연수_2012_2019":근속연수_2012_2019, "근속연수_2020년이후":근속연수_2020년이후},
              {"임원퇴직소득한도": total,
               "배수": "2012~2019년 3배수, 2020년 이후 2배수",
               "초과분처리": "한도 초과분은 퇴직소득이 아니라 근로소득으로 과세되며 IRP에 입금할 수 없습니다."},
              "doc45 임원 퇴직소득 한도 안내")

REGISTRY = {
    "tax_credit": tax_credit,
    "pension_withdrawal_limit": pension_withdrawal_limit,
    "연금수령연차_기산": 연금수령연차_기산,
    "연금수령요건": 연금수령요건,
    "retirement_tax_reduction": retirement_tax_reduction,
    "pension_income_tax_rate": pension_income_tax_rate,
    "comprehensive_taxation": comprehensive_taxation,
    "executive_severance_limit": executive_severance_limit,
}
