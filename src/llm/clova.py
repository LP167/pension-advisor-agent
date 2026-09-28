# -*- coding: utf-8 -*-
"""HyperCLOVA X — CLOVA Studio Chat Completions V3.
규정: 최종 답변 생성은 반드시 이 클라이언트를 경유한다.

엔드포인트 (공식 문서 기준)
  POST https://clovastudio.stream.ntruss.com/v3/chat-completions/{modelName}
  Authorization: Bearer {API Key}
  X-NCP-CLOVASTUDIO-REQUEST-ID: {요청 ID}
응답: result.message.content

약관 제4조 ③ — 모델 지원이 사전 통지 후 종료·변경될 수 있음.
09.06 코드 동결 후 24일간 무중단이 요구되므로 모델명을 환경변수로 분리하고
1차 모델 실패 시 폴백 모델로 자동 전환한다.
"""
import os, json, uuid, time, logging, ssl
import urllib.request, urllib.error
from typing import List, Dict, Optional

# 운영(Docker python:3.11)은 httpx를 쓴다.
# 로컬 개발 환경에 httpx가 없으면 표준 라이브러리로 자동 대체한다.
# (파이썬 3.14 등 최신 인터프리터에서 일부 패키지 휠이 없어 설치가 막히는 경우 대비)
try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    httpx = None
    _HAS_HTTPX = False

def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()
from .base import LLMClient

log = logging.getLogger(__name__)

class HTTPStatus(Exception):
    """httpx 부재 시 HTTP 오류를 동일한 형태로 다루기 위한 래퍼."""
    def __init__(self, code: int, text: str):
        super().__init__(f"HTTP {code}: {text[:200]}")
        self.code, self.text = code, text

class ClovaLLM(LLMClient):
    name = "hyperclova-x"

    def __init__(self):
        self.key     = os.environ["CLOVA_API_KEY"].strip()
        self.host    = os.getenv("CLOVA_HOST", "https://clovastudio.stream.ntruss.com").rstrip("/")
        self.model   = os.getenv("CLOVA_MODEL", "HCX-005").strip()
        self.fallback= [m.strip() for m in os.getenv("CLOVA_FALLBACK_MODELS", "HCX-DASH-002").split(",") if m.strip()]
        self.timeout = float(os.getenv("CLOVA_TIMEOUT", "25"))
        self.retries = int(os.getenv("CLOVA_RETRIES", "2"))
        # 한 번의 chat() 이 LLM 호출에 쓸 수 있는 전체 벽시계 예산(초).
        # main.py 가 ANSWER_TIMEOUT(기본 240초)으로 요청을 끊는데, 그 경로는
        # retrieved_context 를 빈 문자열로 반환한다 — 파이프라인 자체 폴백(근거 포함)보다 나쁘다.
        # 그래서 LLM 재시도는 240초를 건드리기 전에 스스로 포기해야 한다.
        self.budget  = float(os.getenv("CLOVA_BUDGET", "180"))

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.key}",
            "X-NCP-CLOVASTUDIO-REQUEST-ID": uuid.uuid4().hex,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _call(self, model: str, payload: dict, timeout: Optional[float] = None) -> str:
        to = self.timeout if timeout is None else max(1.0, timeout)
        url = f"{self.host}/v3/chat-completions/{model}"
        if _HAS_HTTPX:
            with httpx.Client(timeout=to) as c:
                r = c.post(url, headers=self._headers(), json=payload)
                r.raise_for_status()
                d = r.json()
        else:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                         headers=self._headers())
            try:
                with urllib.request.urlopen(req, timeout=to, context=_ssl_context()) as r:
                    d = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                raise HTTPStatus(e.code, e.read().decode("utf-8", "ignore")) from e
        status = (d.get("status") or {}).get("code")
        if status not in (None, "20000"):
            raise RuntimeError(f"CLOVA status={status} msg={(d.get('status') or {}).get('message')}")
        return ((d.get("result") or {}).get("message") or {}).get("content", "")

    def chat(self, messages: List[Dict[str, str]], *, temperature: float = 0.1,
             max_tokens: int = 1024, stop: Optional[List[str]] = None) -> str:
        payload = {
            "messages": messages,
            "temperature": max(min(temperature, 1.0), 0.01),   # 0 < t <= 1
            "topP": 0.8,
            "topK": 0,
            "maxTokens": min(max_tokens, 4096),
            "repetitionPenalty": 1.1,
            "stopBefore": stop or [],
            "includeAiFilters": False,
        }
        last = None
        deadline = time.time() + self.budget
        for model in [self.model] + self.fallback:
            for attempt in range(self.retries + 1):
                remain = deadline - time.time()
                if remain < 5.0:                          # 남은 예산으로 의미 있는 시도 불가
                    log.warning("CLOVA 시간예산 %.0fs 소진 → 상위 폴백에 위임", self.budget)
                    break
                try:
                    out = self._call(model, payload, timeout=min(self.timeout, remain))
                    if model != self.model:
                        log.warning("CLOVA fallback model used: %s", model)
                    return out
                except Exception as e:
                    last = e
                    code = None
                    if _HAS_HTTPX and isinstance(e, httpx.HTTPStatusError):
                        code = e.response.status_code
                    elif isinstance(e, HTTPStatus):
                        code = e.code
                    # 재시도가 무의미한 것은 상태코드로 확인되는 4xx 뿐이다.
                    # 그 밖(타임아웃·연결 끊김·게이트웨이 status 오류 = code None)은
                    # 전형적인 일시 장애이므로 재시도와 모델 폴백을 태운다.
                    #
                    # 09.05 이전 결함 — 여기서 `if code is None: raise` 로 즉시 탈출했다.
                    #   그래서 CLOVA_RETRIES / CLOVA_FALLBACK_MODELS 가 타임아웃 경로에서
                    #   전혀 작동하지 않았다(dev B-081: 60.1초 1회 시도 후 즉시 폴백).
                    #   운영 Docker 는 httpx 가 있어 httpx.ReadTimeout 으로 오는데 이 또한
                    #   HTTPStatusError 가 아니므로 같은 경로였다.
                    #   또한 아래에 있던 두 번째 `except Exception` 은 첫 핸들러가 모두
                    #   삼키므로 도달 불가능한 죽은 코드였다. 그것을 이 한 갈래로 합쳤다.
                    if code in (400, 401, 403, 404):      # 재시도 무의미 → 다음 모델
                        log.error("CLOVA %s %s", model, e)
                        break
                    log.warning("CLOVA %s 시도 %d 실패(%s) → 재시도",
                                model, attempt + 1, type(e).__name__)
                    time.sleep(min(0.6 * (attempt + 1), max(0.0, deadline - time.time())))
        raise RuntimeError(f"CLOVA call failed on all models: {type(last).__name__}: {last}")
