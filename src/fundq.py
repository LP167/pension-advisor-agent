# -*- coding: utf-8 -*-
"""정형 펀드 검색 — 투자설명서 본문(BM25) 대신 펀드 마스터 레코드를 근거로 쓰는 경로.

상품 비교 질의에서 BM25 본문 검색만 쓰면 세 가지가 무너진다.
 ① 같은 시리즈(단기/중장기/장기)는 본문이 거의 동일해 상위 청크가 한 펀드로 쏠린다.
 ② 보수·위험등급은 표 안의 숫자라 청크 경계에서 잘린다.
 ③ 비교의 축(무엇이 다른가)을 모델이 스스로 골라야 해서 답이 흔들린다.
정형 레코드로 답하면 비교 축이 고정되고, 숫자는 원문 표 라인까지 인용된다.
"""
import re, json
from typing import List, Dict, Any, Optional, Tuple

# 듀레이션은 부분문자열 포함관계가 있어(단기 ⊂ 초단기·중장기) 최장일치로만 판정한다.
DUR = re.compile(r"초단기|중장기|중단기|장기|중기|단기")
RISK_WORD = {r"매우\s*높은\s*위험|초고위험": 1, r"높은\s*위험|고위험": 2, r"다소\s*높은\s*위험": 3,
             r"보통\s*위험|중위험": 4, r"낮은\s*위험|저위험": 5, r"매우\s*낮은\s*위험|초저위험": 6}
ASSET = {r"주식형|주식에|국내주식|해외주식": "주식형", r"채권형|채권에": "채권형",
         r"혼합|주식혼합|채권혼합": "혼합형", r"재간접": "재간접", r"단기금융|MMF": "단기금융"}
HOUSE = ["미래에셋","삼성","KB","한국투자","신한","키움","한화","교보악사","하나","NH아문디","우리",
         "이스트스프링","마이다스","베어링","슈로더","피델리티","블랙록","IBK","DB","흥국","BNK","유진","현대"]
# 계좌 맥락 → 우선 표시할 클래스코드
ACCOUNT_CLASS = [(r"퇴직연금|IRP|개인형퇴직연금|\bDC\b|\bDB\b", ["C-P2","S-P2"], "퇴직연금 전용 클래스"),
                 (r"연금저축|개인연금", ["C-P","S-P","C-Pe"], "개인연금 전용 클래스")]
CHANNEL = [(r"온라인|비대면|인터넷|앱", "온라인"), (r"오프라인|창구|지점|대면", "오프라인")]
# 위험감내는 적합성(suitability) 판정에 직결되므로 하드 필터로 쓴다.
# "원금 손실은 피하고 싶다"는 고객에게 1등급(매우 높은 위험) 상품을 제시하면 그 자체로 오답이다.
RISK_TOLERANCE = [(r"공격적|적극적|고수익|수익.{0,6}(극대|많이)|위험.{0,6}(감내|감수|선호)", [1, 2, 3]),
                  (r"중립|중간|적당|균형", [3, 4, 5]),
                  (r"안정|보수적|안전|저위험|원금.{0,3}보장|"
                   r"(원금|손실)[^.?!]{0,14}(피하|싫|없|않|최소|보존|지키|원치|불안)", [5, 6])]

STOP = set("""펀드 상품 투자 신탁 증권 자산 운용 계좌 가입 수익 수익률 비교 차이 설명 알려 주세요 뭐가 어떤
무엇 얼마 어떻게 다른 다릅니까 다른가요 있나요 인가요 되나요 그리고 하고 이랑 추천 좋은 좋을 괜찮은
저는 제가 지금 요즘 정도 경우 관련 대해 대한 위해 보수 등급 위험 총보수 수수료 클래스 종류 기준""".split())
TOKEN = re.compile(r"[가-힣]{2,}|[A-Za-z]{2,}")


def _norm(s: str) -> str:
    return re.sub(r"[\s·,\-]", "", s or "")

def _core(name: str) -> str:
    """비교에 쓸모없는 상용어를 걷어낸 펀드명 핵심부."""
    n = _norm(name)
    n = re.sub(r"증권|자?투자신탁|투자회사|제?\d+호|\[.*?\]|\(.*?\)", "", n)
    return n

# 듀레이션(만기 구간)은 채권 개념이다. 주식형 펀드명의 "장기성장", "단기매매" 같은 표현을
# 듀레이션으로 읽으면 "장기 국공채" 질의에 주식형이 섞여 들어온다.
DUR_ASSETS = {"채권형", "혼합형", "단기금융", "MMF"}
def fund_duration(name: str, asset: Optional[str] = None) -> Optional[str]:
    if asset is not None and asset not in DUR_ASSETS: return None
    m = DUR.search(_norm(name))
    return m.group(0) if m else None


class FundQuery:
    def __init__(self, master: List[Dict[str, Any]]):
        self.master = master
        for r in self.master:
            r["_core"] = _core(r.get("펀드명"))
            r["_dur"] = fund_duration(r.get("펀드명"), r.get("자산유형"))

    # ── 질의 → 정형 조건 ────────────────────────────────────────────
    def parse(self, q: str) -> Dict[str, Any]:
        qn = _norm(q)
        f: Dict[str, Any] = {}
        durs = []
        i = 0
        while i < len(qn):
            m = DUR.match(qn, i)
            if m: durs.append(m.group(0)); i = m.end()
            else: i += 1
        if durs: f["듀레이션"] = sorted(set(durs))
        g = re.search(r"([1-6])\s*등급", q)
        if g: f["위험등급"] = [int(g.group(1))]
        else:
            for pat, lv in RISK_WORD.items():
                if re.search(pat, q): f["위험등급"] = [lv]; break
        for pat, a in ASSET.items():
            if re.search(pat, q): f["자산유형"] = a; break
        houses = [h for h in HOUSE if _norm(h) in qn]
        if houses: f["운용사"] = houses
        for pat, codes, label in ACCOUNT_CLASS:
            if re.search(pat, q, re.I): f["계좌클래스"] = {"codes": codes, "label": label}; break
        for pat, ch in CHANNEL:
            if re.search(pat, q): f["채널"] = ch; break
        for pat, lvs in RISK_TOLERANCE:
            if re.search(pat, q): f["위험감내등급"] = lvs; break
        if re.search(r"보수.{0,6}(낮|싼|저렴|적은)|비용.{0,6}(낮|적)", q): f["정렬"] = "보수낮은순"
        elif re.search(r"보수.{0,6}(높|비싼|많)", q): f["정렬"] = "보수높은순"
        f["비교요청"] = bool(re.search(r"비교|차이|다른가|다릅|어느\s*쪽|무엇이\s*다", q)) or len(durs) >= 2
        # 계좌·채널 맥락어("퇴직연금 계좌인데")가 펀드명 매칭으로 새면 이름에 '퇴직연금'이 든
        # 상품만 상위로 끌려온다. 조건으로만 쓰고 명칭 매칭 대상에서는 제외한다.
        f["_ctxwords"] = [w for w in ("퇴직연금","개인형퇴직연금","IRP","연금저축","개인연금","DC","DB",
                                      "온라인","오프라인","비대면","창구","지점","대면") if w in q]
        f["키워드"] = [t for t in TOKEN.findall(q)
                     if t not in STOP and len(t) >= 2 and t not in f["_ctxwords"]]
        return f

    # ── 정형 조건 → 레코드 ──────────────────────────────────────────
    def match(self, q: str, limit: int = 6) -> Dict[str, Any]:
        f = self.parse(q)
        qn = _norm(q)
        for w in f.get("_ctxwords") or []: qn = qn.replace(_norm(w), "")
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for r in self.master:
            s, why = 0.0, []
            core = r["_core"]
            # 펀드명 부분일치 (긴 조각일수록 가중)
            for L in range(len(core), 3, -1):
                hit = next((core[i:i+L] for i in range(len(core)-L+1) if core[i:i+L] in qn), None)
                if hit: s += L * 1.0; why.append(f"명칭일치:{hit}"); break
            for kw in f["키워드"]:
                if _norm(kw) and _norm(kw) in core: s += len(kw) * 0.8; why.append(f"키워드:{kw}")
            if f.get("운용사") and any(_norm(h) in _norm(r.get("운용사") or "") + core for h in f["운용사"]):
                s += 2.0; why.append("운용사일치")
            # 자산유형·위험등급·듀레이션은 질의에 명시되면 하드 필터다.
            # 가점으로만 쓰면 "채권형 5등급" 질의에 주식형 1등급이 섞여 들어온다.
            if f.get("자산유형"):
                if r.get("자산유형") != f["자산유형"]: continue
                s += 1.5; why.append("자산유형일치")
            if f.get("위험등급"):
                if r.get("위험등급") not in f["위험등급"]: continue
                s += 2.0; why.append("위험등급일치")
            if f.get("위험감내등급"):
                if r.get("위험등급") not in f["위험감내등급"]: continue
                s += 1.5; why.append("위험감내부합")
            if f.get("듀레이션"):
                if r["_dur"] not in f["듀레이션"]: continue
                s += 2.0; why.append(f"듀레이션:{r['_dur']}")
            if s <= 0: continue
            scored.append((s, dict(r, _why=why, _score=round(s, 2))))
        scored.sort(key=lambda x: -x[0])
        named = any("명칭일치" in w or w.startswith("키워드") for _, r in scored[:1] for w in r["_why"])
        top = [r for _, r in scored[:limit]]
        if named and top and top[0]["_score"] >= 6:
            # 특정 상품이 지목된 질의 — 동급 후보만 남기고 주변 상품은 잘라낸다
            top = [r for r in top if r["_score"] >= top[0]["_score"] * 0.7]
        if not named and not f.get("정렬"):
            # 스크리닝 질의(조건만 주어짐)는 비용 낮은 순이 기본 제시 순서
            f["정렬"] = "보수낮은순(기본)"
        if f.get("정렬"):
            key = lambda r: min([c["총보수_pct"] for c in r["클래스"] if c.get("총보수_pct") is not None] or [99])
            top.sort(key=key, reverse=(f["정렬"] == "보수높은순"))
        f["검색모드"] = "명칭지목" if named else "조건스크리닝"
        return {"filters": {k: v for k, v in f.items() if k not in ("키워드", "_ctxwords")},
                "keywords": f["키워드"], "matched": len(scored),
                "funds": top, "isins": [r["isin"] for r in top],
                "compare": bool(f["비교요청"] and len(top) >= 2)}

    # ── 레코드 → 근거 텍스트 ────────────────────────────────────────
    @staticmethod
    def _pick_classes(r: Dict[str, Any], f: Dict[str, Any], k: int = 4,
                      pin: Optional[str] = None) -> List[Dict[str, Any]]:
        """pin: 비교 기준 클래스코드. 비교 질의에서 펀드마다 다른 클래스를 보여주면
        0.46% vs 0.275% 같은 성립하지 않는 대조가 근거에 남는다."""
        cls = [c for c in r.get("클래스") or [] if c.get("총보수_pct") is not None]
        acc = (f.get("계좌클래스") or {}).get("codes")
        ch = f.get("채널")
        def rank(c):
            p = 0
            if pin and c.get("클래스코드") == pin: p -= 10
            if acc and (c.get("클래스코드") in acc): p -= 4
            if ch and ch in (c.get("클래스명") or ""): p -= 2
            return (p, c["총보수_pct"])
        return sorted(cls, key=rank)[:k]

    def _cite(self, r: Dict[str, Any]) -> str:
        pg = r.get("보수표페이지")
        return r["원문파일"] + (f" p.{pg}" if pg else "") + " (간이투자설명서 투자비용표)"

    @staticmethod
    def _class_line(c: Dict[str, Any], compact: bool) -> str:
        name = (c.get("클래스명") or "클래스") + (f"({c['클래스코드']})" if c.get("클래스코드") else "")
        seg = [f"  - {name}: 총보수 {c['총보수_pct']}%"]
        if c.get("총보수비용_pct") is not None: seg.append(f"총보수·비용 {c['총보수비용_pct']}%")
        ex = c.get("투자기간별비용_천원")
        if compact:
            if c.get("선취판매수수료_pct") is not None: seg.append(f"선취 납입금액의 {c['선취판매수수료_pct']}%")
            if ex and len(ex) == 5: seg.append(f"1,000만원 투자 시 1년 {ex[0]}천원·10년 {ex[4]}천원")
            return " · ".join(seg)
        if c.get("판매보수_pct") is not None: seg.append(f"판매보수 {c['판매보수_pct']}%")
        if c.get("동종유형총보수_pct") is not None: seg.append(f"동종유형 평균 {c['동종유형총보수_pct']}%")
        if c.get("선취판매수수료_pct") is not None: seg.append(f"선취판매수수료 납입금액의 {c['선취판매수수료_pct']}%")
        if ex and len(ex) == 5:
            seg.append("1,000만원 투자 시 누적비용 1년 {0}천원·2년 {1}천원·3년 {2}천원·5년 {3}천원·10년 {4}천원".format(*ex))
        return " / ".join(seg) + f"\n    (원문 표: {c['원문']})"

    def render(self, mq: Dict[str, Any], start: int = 1, compact: bool = True) -> Tuple[str, List[str]]:
        """펀드 마스터 레코드를 근거 블록으로 직렬화.

        compact=True 가 기본이다. 원문 표 라인("- 0.46 0.26 - 0.47 48 98 151 264 587")을 그대로
        넣으면 근거 완전성의 precision 축에서 손해를 본다. 평가 지표는 recall 뿐 아니라
        '질의와 무관한 근거를 배제했는가'도 채점하므로, 질의에 필요한 필드만 남기고
        근거 위치는 페이지 단위 인용으로 대신한다.
        """
        f = mq["filters"]
        k = (2 if mq.get("compare") else 3) if compact else 4
        pin = self._common_class(mq["funds"], f)[0] if mq.get("compare") else None
        out, ids = [], []
        for i, r in enumerate(mq["funds"], start):
            cls = self._pick_classes(r, f, k=k, pin=pin)
            head = f"[근거{i}] 펀드마스터 · {self._cite(r)} · 작성기준일 {r['작성기준일']}"
            line = f"{r['펀드명']} (ISIN {r['isin']}) | {r.get('자산유형')} | " \
                   f"위험등급 {r['위험등급']}등급[{r['위험등급라벨']}] | 운용사 {r['운용사']}"
            if r.get("_dur"): line += f" | 듀레이션 {r['_dur']}"
            body = [line]
            if r.get("벤치마크"): body.append(f"비교지수: {r['벤치마크'][:80]}")
            strat = r.get("투자전략요약")
            if strat:
                strat = re.sub(r"^투자전략\s*", "", strat)
                body.append(f"투자전략: {strat[:120 if compact else 220]}")
            if cls:
                body.append("클래스별 보수·비용:" if not compact else "보수(클래스별):")
                body += [self._class_line(c, compact) for c in cls]
            out.append(head + "\n" + "\n".join(body))
            ids.append(f"{r['isin']}#master")
        return "\n\n".join(out), ids

    # ── 비교축 요약 (LLM 이 비교 기준을 임의로 고르지 않게 고정) ──────
    CLASS_PRIORITY = ["C-P2", "S-P2", "C-P", "S-P", "C-Pe", "A", "C", "A-e", "C-e", "C1", "R", "S"]

    def _common_class(self, funds, f) -> Tuple[Optional[str], bool]:
        """비교는 같은 클래스끼리 해야 성립한다. 전 펀드에 공통으로 존재하는 클래스코드를 고른다."""
        sets = [{c.get("클래스코드") for c in (r.get("클래스") or [])
                 if c.get("클래스코드") and c.get("총보수_pct") is not None} for r in funds]
        common = set.intersection(*sets) if sets and all(sets) else set()
        pri = ((f.get("계좌클래스") or {}).get("codes") or []) + self.CLASS_PRIORITY
        for code in pri:
            if code in common: return code, True
        return None, False

    def compare_axes(self, mq: Dict[str, Any]) -> Optional[str]:
        fs = mq["funds"]
        if len(fs) < 2: return None
        f = mq["filters"]
        code, common = self._common_class(fs, f)
        rows = []
        for r in fs:
            cls = [c for c in (r.get("클래스") or []) if c.get("총보수_pct") is not None]
            c = next((x for x in cls if x.get("클래스코드") == code), None) if common else None
            if c is None: c = self._pick_classes(r, f, k=1)[0] if cls else {}
            rows.append({"펀드명": r["펀드명"], "위험등급": r["위험등급"], "듀레이션": r.get("_dur"),
                         "클래스": (c.get("클래스명") or "") + (f"({c['클래스코드']})" if c.get("클래스코드") else ""),
                         "총보수_pct": c.get("총보수_pct"), "총보수비용_pct": c.get("총보수비용_pct"),
                         "선취판매수수료_pct": c.get("선취판매수수료_pct"),
                         "작성기준일": r["작성기준일"]})
        same_risk = len({r["위험등급"] for r in fs}) == 1
        notes = []
        if same_risk and f.get("검색모드") == "명칭지목":
            notes.append(f"비교 대상 {len(fs)}종의 위험등급이 {fs[0]['위험등급']}등급으로 모두 같습니다. "
                         "위험등급만으로는 구분되지 않으므로 듀레이션(만기 구간)과 보수 수준으로 구분해야 합니다.")
        elif same_risk:
            notes.append(f"질의 조건이 {fs[0]['위험등급']}등급이므로 위험등급은 구분축이 되지 않습니다. 보수 수준으로 비교합니다.")
        else:
            notes.append("위험등급이 서로 달라 위험등급이 1차 구분축입니다.")
        if common:
            notes.append(f"보수는 전 상품에 공통으로 존재하는 {code} 클래스 기준으로 비교했습니다.")
        else:
            notes.append("공통 클래스가 없어 상품별로 클래스가 다릅니다. 클래스가 다르면 보수를 직접 비교할 수 없으므로 "
                         "클래스명을 함께 밝혀야 합니다.")
        dates = sorted({r["작성기준일"] for r in fs})
        if len(dates) > 1:
            notes.append(f"작성기준일이 {dates[0]} ~ {dates[-1]} 로 서로 달라 동일 시점 비교가 아닙니다.")
        return json.dumps({"비교축": ["듀레이션", "위험등급", "총보수", "총보수·비용"],
                           "비교기준클래스": code if common else None,
                           "동일위험등급": same_risk, "판단메모": notes, "행": rows},
                          ensure_ascii=False, indent=1)
