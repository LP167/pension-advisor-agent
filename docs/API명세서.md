# API 명세서 — 연금 Agent

제10회 2026 미래에셋증권 AI Festival · 연금 Agent 트랙
버전 1.0 · 최종 갱신 2026.09.05

---

## 1. 개요

| 항목 | 값 |
|---|---|
| Base URL | `http://{SERVER_IP}` (HTTP 표준 포트 80) |
| Base URL (호환) | `http://{SERVER_IP}:8000` — 컨테이너가 `80:8000`·`8000:8000` 을 동시 매핑 |
| 프로토콜 | HTTP/1.1 |
| 인증 | 없음 (평가 시스템 직접 호출) |
| 인코딩 | UTF-8 |
| Content-Type | `application/json` |
| 동시성 | 순차 1건 (주최측 공식 안내) |
| 문항당 상한 | 300초 (주최측) / 서버 내부 상한 240초 |

---

## 2. `GET /answer` — 평가용 엔드포인트

### 2-1. 요청

```
GET /answer?question_id={question_id}&question={question}
```

| 파라미터 | 위치 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| `question_id` | query | string | ✅ | 문항 식별자. 응답에 그대로 반환된다 |
| `question` | query | string | ✅ | 자연어 질문. URL 인코딩 필요 |

**예시**

```bash
curl -G "http://{SERVER_IP}/answer" \
     --data-urlencode "question_id=Q001" \
     --data-urlencode "question=연금저축과 IRP의 세액공제 한도가 어떻게 다른가요?"
```

### 2-2. 응답 (200 OK)

주최측 규격 **4필드 고정**. 모든 필드는 **string** 이다.

```json
{
  "question_id": "Q001",
  "question": "연금저축과 IRP의 세액공제 한도가 어떻게 다른가요?",
  "retrieved_context": "…답변 생성에 참고한 검색 문서…",
  "think_trace": "…추론 과정 + 검증 증명서 JSON…",
  "answer": "…최종 답변…"
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `question_id` | string | 요청값 그대로 반환 |
| `question` | string | 요청값 그대로 반환 |
| `retrieved_context` | string | 검색된 근거 청크. 출처 문서명·조문이 함께 표기된다 |
| `think_trace` | string | 자연어 요약 + 검증 증명서 JSON 직렬화 (아래 §3) |
| `answer` | string | 최종 답변. 미검증 수치·문장은 강등 표시가 붙는다 |

> 길이 제한은 주최측이 두지 않는다고 공식 확인했다(2026.09.01).
> 실측값: `think_trace` 중앙값 2,496자 / p95 4,518자,
> `retrieved_context` 중앙값 1,884자 / p95 3,512자, `answer` 중앙값 431자 / p95 1,028자.

### 2-3. 예외 시 응답

**이 API 는 5xx 를 반환하지 않는다.** 타임아웃·내부 예외에서도 200과 4필드를 유지하고,
사유는 `think_trace` 에 남긴다. 평가 시스템의 재시도 예산을 낭비하지 않기 위한 설계다.

| 상황 | HTTP | `think_trace` | `answer` |
|---|---|---|---|
| 처리 시간 초과 (`ANSWER_TIMEOUT`, 기본 240초) | 200 | `{"stop":"timeout"}` | 시간 초과 안내 문구 |
| 내부 예외 | 200 | `{"stop":"error","type":"<예외클래스명>"}` | 일시 오류 안내 문구 |

두 경우 모두 `retrieved_context` 는 빈 문자열이다.

**FastAPI 기본 동작으로 발생하는 것:** 필수 쿼리 파라미터 누락 시 `422 Unprocessable
Entity` (FastAPI 검증). 평가 호출은 항상 두 파라미터를 포함하므로 정상 경로에서는
발생하지 않는다.

### 2-4. 지연 실측 (dev 212문항, HCX-005 실호출, 2026.09.01)

| 지표 | 값 |
|---|---|
| p50 | 4,615 ms |
| p95 | 10,050 ms |
| max | 21,406 ms |

주최측 상한 300초 대비 최대값 기준 **7% 수준**이다.

---

## 3. `think_trace` 구조

`think_trace` 는 **string** 이다. 내용은 자연어 요약과 검증 증명서 JSON 의 하이브리드로,
사람이 읽어도 판단 경로가 보이고 기계로 파싱해도 구조가 남도록 했다.

포함되는 주요 키:

| 키 | 설명 |
|---|---|
| `route` | 6분류 라우팅 결과 (제도 / 세제 / 종합 / 기타절차 / 상품설명비교 / 조건부추천) |
| `slots` | 판단에 필요한 정보 슬롯과 충족 여부. 미충족 시 역질문으로 전환 |
| `premise` | 질문 전제 오류 탐지·교정 내역 |
| `tools` | 호출한 결정론적 계산기와 입력·출력, `rule_source` |
| `evidence` | 사용한 근거 청크 id 와 출처 |
| `certificate` | 검증 증명서 (아래) |
| `stop` | 정상 종료 사유 또는 `timeout` / `error` |

### 검증 증명서 (Answer Assurance Certificate)

| 단계 | 이름 | 검사 내용 |
|---|---|---|
| **L0** | `schema` | 응답 형식·필수 필드 충족 |
| **L1** | `policy` | 금지 표현, 단정적 투자권유, 개인정보 누출 |
| **L2** | `fact` | 답변의 각 수치·진술을 **자기 인용 근거와 1:1 대조**. 불일치 1건이라도 있으면 강등 |
| **L3** | `intent` | 질문의 목표를 실제로 충족했는지 |

각 단계는 `pass` / `fail` 과 사유를 남기고, 종합 `confidence` 가 함께 기록된다.

**L2 가 다른 시스템과 다른 점:** 근거 전체를 하나의 수 풀로 합쳐 "어딘가 있으면 통과"
시키지 않는다. 이 방식은 ① 다른 근거의 숫자를 자기 인용에 붙이기 ② OCR 로 값이 소실된
구간을 인용하며 수치 날조 ③ 수치 없는 서술의 검증 누락 — 세 경로를 열어 두며, 실제로
증명서 4단계를 모두 통과하면서 confidence 0.94~0.98 로 틀리는 **침묵 실패**를 만들었다.
현재 구현은 인용 단위로 대조하고, 관계 전도(`rules.py` 7규칙)와 미접지 판단까지 잡는다.

---

## 4. `GET /health` — 상태 확인

### 요청

```
GET /health
```

### 응답 (200 OK)

```json
{ "status": "ok", "llm": "hyperclova-x", "chunks": 10965 }
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `status` | string | 항상 `"ok"` (응답 자체가 가용성 신호) |
| `llm` | string | 활성 LLM 어댑터명. 정상 배포 시 `hyperclova-x` |
| `chunks` | int | 로드된 인덱스 청크 수. 정상값 `10965` |

컨테이너 헬스체크가 30초 주기로 이 엔드포인트를 호출한다
(`timeout=10s`, `start_period=120s`, `retries=5`).

> **배포 직후 반드시 확인할 것:** `llm` 이 `hyperclova-x` 가 아니라 `mock` 이면
> `CLOVA_API_KEY` 가 컨테이너에 전달되지 않은 것이다. `chunks` 가 0이면 인덱스가
> 마운트되지 않은 것이다.

---

## 5. LLM 연동 규격 (내부)

| 항목 | 값 |
|---|---|
| 엔드포인트 | `POST {CLOVA_HOST}/v3/chat-completions/{model}` |
| 인증 헤더 | `Authorization: Bearer {CLOVA_API_KEY}` |
| 요청 ID 헤더 | `X-NCP-CLOVASTUDIO-REQUEST-ID` |
| 응답 경로 | `result.message.content` |
| 기본 모델 | `HCX-005` |
| 폴백 | `HCX-DASH-002` (기본 모델 실패 시) |
| 재시도 | `CLOVA_RETRIES=1` → 모델당 2회 시도. 4xx(400·401·403·404)는 재시도하지 않고 즉시 폴백 모델로 넘어간다. 그 밖의 실패(타임아웃·연결 끊김·게이트웨이 오류·429·5xx)는 백오프 후 재시도한다 |
| 호출 상한 | `CLOVA_TIMEOUT=60`초 (남은 예산이 더 적으면 그만큼으로 축소) |
| 시간 예산 | `CLOVA_BUDGET=180`초. 한 문항의 LLM 호출 전체 벽시계 상한. 소진 시 재시도를 중단하고 근거를 담은 폴백 답변으로 전환한다 |
| 최악 경로 | `HCX-005` 60초×2 → `HCX-DASH-002` 58.2초 = **180초**. `ANSWER_TIMEOUT` 240초까지 60초, 주최측 상한 300초까지 120초 여유 |

LLM 호출은 어댑터(`src/llm/clova.py`)로 격리되어 있고, `--llm mock` 으로 실호출 없이
전체 파이프라인을 회귀 검증할 수 있다.

---

## 6. 운영 정보

| 항목 | 값 | 근거 |
|---|---|---|
| 워커 수 | 1 | 워커마다 인덱스 전체 로드(RSS 771MB × N). 평가는 순차 1건 |
| 동시 연결 상한 | 32 | `--limit-concurrency 32` |
| Keep-alive | 15초 | |
| 메모리 제한 | 2 GB | 실측 RSS 771MB 대비 2.6배 |
| 재시작 정책 | `always` | 답변 수집 09.07 10:00~09.11 15:00 무중단 요구 |
| 종료 유예 | 30초 | 진행 중 문항 완료 후 종료 |
| 로그 | json-file, 20MB × 5 | 장기 가동 누적 시 디스크 보호 |
