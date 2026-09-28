# -*- coding: utf-8 -*-
"""HyperCLOVA X 실호출 스모크 테스트.
사용법:  python3 scripts/07_smoke_clova.py
API 키는 화면에 출력하지 않는다. 호출 성공 여부·모델·토큰수·지연시간만 보고한다.
"""
import os, sys, json, time, uuid, ssl, urllib.request, urllib.error

# macOS python.org 배포판은 CA 번들이 등록되지 않아 SSL 검증에 실패한다.
# 운영 코드(httpx)는 certifi를 내장하므로, 여기서도 동일하게 certifi를 우선 사용한다.
def make_ssl_context():
    try:
        import certifi
        print(f"[i] certifi 사용: {certifi.where()}")
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        print("[!] certifi 미설치 — 시스템 인증서로 시도합니다.")
        print("    실패하면: python3 -m pip install --upgrade certifi")
        return ssl.create_default_context()

SSLCTX = make_ssl_context()

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV  = os.path.join(BASE, ".env")
if not os.path.exists(ENV):
    sys.exit("[X] .env 파일이 없습니다.")
kv = {}
for line in open(ENV, encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); kv[k.strip()] = v.strip()

KEY   = kv.get("CLOVA_API_KEY", "")
HOST  = kv.get("CLOVA_HOST", "https://clovastudio.stream.ntruss.com").rstrip("/")
MODEL = kv.get("CLOVA_MODEL", "HCX-005")
FALL  = [m for m in kv.get("CLOVA_FALLBACK_MODELS", "HCX-DASH-002").split(",") if m.strip()]

if not KEY:
    sys.exit("[X] CLOVA_API_KEY 가 비어 있습니다.")
print(f"[i] 키 길이 {len(KEY)} / 접두 {KEY[:3]}...  (값은 출력하지 않음)")
print(f"[i] host={HOST}  model={MODEL}  fallback={FALL}")

CASES = [
    ("연결확인", [{"role": "user", "content": "한 단어로만 답하세요. 대한민국의 수도는?"}]),
    ("근거인용", [
        {"role": "system", "content": "주어진 [근거]에만 근거해 답하고, 문장 끝에 [근거1]을 표기하십시오. 근거에 없는 내용은 만들지 마십시오."},
        {"role": "user", "content": "[근거]\n[근거1] doc41 p.1 — 세액공제 납입한도는 연금저축 연 600만원, IRP는 연금저축 납입액을 포함해 연 900만원이다.\n\n[질의]\n연금저축과 IRP를 합쳐서 세액공제는 얼마까지 되나요?"}]),
]

def call(model, messages):
    url = f"{HOST}/v3/chat-completions/{model}"
    payload = {"messages": messages, "temperature": 0.1, "topP": 0.8, "topK": 0,
               "maxTokens": 256, "repetitionPenalty": 1.1, "includeAiFilters": False}
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={
        "Authorization": f"Bearer {KEY}",
        "X-NCP-CLOVASTUDIO-REQUEST-ID": uuid.uuid4().hex,
        "Content-Type": "application/json", "Accept": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=30, context=SSLCTX) as r:
        d = json.loads(r.read().decode("utf-8"))
    return d, int((time.time() - t0) * 1000)

ok_any = False
for model in [MODEL] + FALL:
    print(f"\n===== 모델: {model} =====")
    for name, msgs in CASES:
        try:
            d, ms = call(model, msgs)
            st = (d.get("status") or {}).get("code")
            res = d.get("result") or {}
            content = ((res.get("message") or {}).get("content") or "").strip()
            usage = res.get("usage") or {"inputLength": res.get("inputLength"), "outputLength": res.get("outputLength")}
            print(f"  [OK] {name}  status={st}  {ms}ms  usage={json.dumps(usage, ensure_ascii=False)}")
            print(f"       > {content[:160]}")
            ok_any = True
        except urllib.error.HTTPError as e:
            print(f"  [HTTP {e.code}] {name} -> {e.read().decode('utf-8', 'ignore')[:200]}")
        except Exception as e:
            msg = str(e)
            print(f"  [ERR] {name} -> {type(e).__name__}: {msg[:200]}")
            if "CERTIFICATE_VERIFY_FAILED" in msg:
                print("        └ CA 인증서 문제입니다. API 키·엔드포인트와 무관합니다. 아래 중 하나로 해결하십시오.")
                print("          1) python3 -m pip install --upgrade certifi")
                print("          2) open \"/Applications/Python 3.$(python3 -c 'import sys;print(sys.version_info.minor)')/Install Certificates.command\"")
    if ok_any:
        break

print("\n[결과] " + ("실호출 정상 — ClovaLLM 연동 준비 완료" if ok_any else "실패 — 위 오류 메시지를 그대로 전달해 주십시오"))
