# 연금 Agent — 제10회 2026 미래에셋증권 AI Festival (연금 Agent 트랙)

> **공개본 안내 (2026-09-28)**
> 이 저장소는 대회 제출 저장소(비공개)에서 **코드와 문서만** 옮긴 공개본입니다.
> - `data/`(주최 측 제공 문서 58종을 가공한 코퍼스·인덱스)와 평가 실행 로그(`*.jsonl`)는 주최 측 자료가 포함되어 **제외**했습니다. 그래서 이 저장소만으로는 서버가 바로 뜨지 않습니다. 코퍼스 구축 절차는 `scripts/01~09`에 그대로 남아 있습니다.
> - API 키·서버 접속 키·배포 서버 IP는 제거했습니다(`.env.example`, `{SERVER_IP}` 참고).
> - 평가 수치는 **직접 구축한 held-out 40문항** 기준이며 대회 공식 채점 결과가 아닙니다. 상세: `docs/기술제안서.md`, `eval/report_holdout.md`.


HyperCLOVA X 기반 개인연금 상담 에이전트.
**검증 증명서(Answer Assurance Certificate)** 를 답변마다 발급해, 근거 없이 생성된
문장·수치를 스스로 강등(degrade)하는 구조를 핵심으로 한다.

---

## 1. 요구 환경

| 항목 | 값 |
|---|---|
| Python | 3.11 (Docker 이미지 기준) |
| 컨테이너 | Docker / Docker Compose v2 |
| LLM | **HyperCLOVA X (`HCX-005`)** — CLOVA Studio Chat Completions **V3** |
| 폴백 모델 | `HCX-DASH-002` |
| 외부 통신 | `clovastudio.stream.ntruss.com` (HTTPS 443) **아웃바운드 허용 필수** |
| 메모리 | 컨테이너 2GB 제한 (실측 RSS 771MB) |
| 디스크 | 인덱스 포함 약 2GB |

> ⚠ 방화벽/ACG에서 **아웃바운드를 차단하면 CLOVA 호출이 전부 실패**한다.
> 인바운드는 `80/TCP`·`8000/TCP`(평가용) + `22/TCP`(관리용)만 열면 된다.
> 컨테이너가 `80:8000`·`8000:8000` 을 동시 매핑하므로 두 포트를 모두 연다.

---

## 2. 환경 구성

### 2-1. 환경변수

`.env.example` 을 복사해 `.env` 를 만들고 API 키를 채운다.

```bash
cp .env.example .env
```

```dotenv
CLOVA_API_KEY=                                        # 필수
CLOVA_MODEL=HCX-005
CLOVA_HOST=https://clovastudio.stream.ntruss.com
CLOVA_TIMEOUT=60                                      # 개별 LLM 호출 상한(초)
ANSWER_TIMEOUT=240                                    # 문항 전체 상한(초)
CLOVA_FALLBACK_MODELS=HCX-DASH-002
CLOVA_RETRIES=1                                       # 모델당 시도 = 값+1
CLOVA_BUDGET=180                                      # 한 문항의 LLM 총 시간 예산(초)
```

> `CLOVA_RETRIES=1` + `CLOVA_BUDGET=180` 은 함께 정해진 값이다.
> 호출당 60초이므로 `HCX-005` 2회(120초) → `HCX-DASH-002` 1회(60초) = 180초로,
> 예산 안에서 폴백 모델까지 반드시 도달한다. `2`로 두면 `HCX-005` 3회로 예산을
> 전부 소진해 폴백 모델에 도달하지 못한다.
> 예산 180초는 `ANSWER_TIMEOUT=240` 보다 낮게 잡았다. 240초를 넘기면 `main.py` 의
> 상위 타임아웃 경로로 떨어지는데, 그 경로는 `retrieved_context` 를 빈 문자열로
> 반환하므로 근거를 담은 파이프라인 자체 폴백보다 나쁘다.

`docker-compose.yml` 이 추가로 지정하는 값:

| 변수 | 값 | 의미 |
|---|---|---|
| `MAX_THINK_TRACE` | `0` | `think_trace` 길이 무제한 (0 = 제한 없음) |
| `MAX_CHUNK_CHARS` | `420` | 근거 청크 1건당 최대 문자 수 |

> **`.env` 는 절대 저장소에 커밋하지 않는다.** `.gitignore` 첫 줄이 `.env` 다.

### 2-2. 데이터 인덱스

`data/` 에 사전 구축된 BM25 인덱스(청크 10,965건)와 펀드 마스터(100종)가 포함된다.
원본에서 다시 만들 경우에만 `scripts/` 를 순서대로 실행한다.

```bash
python3 scripts/01_extract_docs.py     # 원문 텍스트 추출
bash    scripts/02_ocr.sh              # 이미지 PDF OCR (tesseract kor)
python3 scripts/03_fund_master.py      # 펀드 마스터 100종 파싱
python3 scripts/04_build_corpus.py     # 코퍼스 정규화·청크 분할
python3 scripts/05_fund_chunks.py      # 펀드 청크 생성
python3 scripts/06_build_index.py      # BM25 인덱스 빌드
```

---

## 3. 실행

### 3-1. Docker (권장 · 평가용 배포 방식)

```bash
docker compose up -d --build
docker compose logs -f pension-agent      # "agent ready | llm=... | chunks=10965"
```

헬스체크는 컨테이너 내부에서 30초 주기로 `/health` 를 호출한다
(`start_period=120s`, `retries=5`). `restart: always` 이므로 프로세스가 죽어도
자동 복구된다.

### 3-2. 로컬 실행 (개발용)

```bash
pip install -r requirements.txt
uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 1
```

> macOS 등에서 `pydantic-core` 빌드가 실패하면 Python 3.11~3.13 을 쓴다.
> LLM 스모크 테스트만 할 경우 `requirements-local.txt`(certifi 만) 로 충분하다.

### 3-3. 동작 확인

```bash
curl "http://localhost:8000/health"
curl -G "http://localhost:8000/answer" \
     --data-urlencode "question_id=T1" \
     --data-urlencode "question=연금저축 세액공제 한도가 얼마인가요?"
```

---

## 4. 평가용 엔드포인트

```
GET http://{SERVER_IP}/answer?question_id={id}&question={질문}
```

주최측 공지에 따라 **HTTP 표준 포트(80)** 로 제공한다.
컨테이너는 `80:8000` 과 `8000:8000` 을 동시에 매핑하므로
`http://{SERVER_IP}:8000/answer` 로도 동일하게 응답한다(호환용 부가 경로).

동작 확인:

```bash
curl "http://{SERVER_IP}/health"
curl -G "http://{SERVER_IP}/answer" \
     --data-urlencode "question_id=T1" \
     --data-urlencode "question=연금저축 세액공제 한도가 얼마인가요?"
```

배포 환경 (2026.09.06 구축):

| 항목 | 값 |
|---|---|
| 인프라 | NAVER Cloud Platform VPC (한국 리전) |
| 서버 | `pension-agent-server` · Standard s2-g3a (2 vCPU / 8GB) · KVM |
| OS | Ubuntu 24.04.1 LTS |
| 스토리지 | 50GB (CB1) |
| 네트워크 | VPC `10.0.0.0/16` · Subnet `10.0.1.0/24` (KR-2, Public) |
| 방화벽(ACG) | inbound `80/TCP`·`8000/TCP` 전체 개방 · `22/TCP` 운영자 IP 한정 / outbound 전체 허용 |
| 런타임 | Docker 29.1.3 · Compose v2.40.3 · `restart: always` |

상세 규격은 **`docs/API명세서.md`** 를 참조한다.

---

## 5. 평가·검증 실행

```bash
python3 eval/run_bench.py --llm real --file benchmark.jsonl   # dev 212문항 (과금)
python3 eval/run_bench.py --llm mock --file benchmark.jsonl   # 무과금 회귀 확인
python3 eval/chaos_test.py                                    # 파괴 테스트 54시나리오 (무과금)
```

결과는 `eval/results_dev.jsonl` 에 저장되고 리포트는 `eval/report_dev.md` 로 나온다.

> `eval/holdout.jsonl`(40문항)은 **일반화 측정 전용**이다. `eval/gap.md` 의 4개 규칙에 따라
> 이 세트로 규칙을 수정하지 않으며, 측정은 1회만 수행한다(08.31 소진).

---

## 6. 디렉터리 구조

```
pension-agent/
├─ src/
│  ├─ main.py          FastAPI 엔드포인트 (/answer, /health)
│  ├─ schema.py        응답 4필드 스키마
│  ├─ pipeline.py      전체 오케스트레이션 · 미검증 문장 강등
│  ├─ router.py        6분류 라우팅 (제도/세제/종합/기타절차/상품설명비교/조건부추천)
│  ├─ retrieval/       BM25 (한국어 문자 n-gram) + 고객어 확장사전
│  ├─ evidence.py      근거 선별·인용 대조
│  ├─ verify.py        검증 증명서 L0 형식 / L1 정책 / L2 팩트 / L3 목표충족
│  ├─ rules.py         관계 전도 탐지 7규칙
│  ├─ calc.py          결정론적 세제·수령액 계산기 8종 (LLM이 계산하지 않는다)
│  ├─ guard.py         출력 위생 · 금지 표현 차단
│  ├─ premise.py       질문 전제 오류 교정
│  ├─ slots.py         역질문 게이트 (라우터와 분리 — 심층 방어)
│  ├─ fundq.py         펀드 마스터 질의
│  ├─ trace.py         think_trace 직렬화 (자연어 요약 + JSON 하이브리드)
│  └─ llm/clova.py     CLOVA Studio V3 어댑터 (Mock-First)
├─ eval/               벤치마크 · 채점 · 파괴 테스트 · 리포트
├─ scripts/            데이터 파이프라인 01~08
├─ data/               인덱스 · 펀드 마스터
├─ docs/               API 명세서 · 기술제안서 · 교차검증 자료
├─ Dockerfile · docker-compose.yml
└─ .env.example
```

---

## 7. 설계상 지켜지는 제약

- **LLM은 HyperCLOVA X만 사용한다.** 다른 모델을 쓰면 평가 제외 대상이다.
- **숫자는 LLM이 계산하지 않는다.** 세제·수령액은 `calc.py` 의 결정론적 계산기가
  산출하고, 각 계산에는 `rule_source`(근거 조문)가 붙는다.
- **근거로 확인되지 않은 문장·수치는 강등된다.** `pipeline._degrade_unverified` 가
  해당 표현에 미검증 표시를 붙이고 단정을 제거한다.
- **역질문 게이트가 라우터와 분리되어 있다.** 라우터 분류가 틀려도 정보 부족 상황에서
  단정하지 않는다(MAST FM-2.2 대응).
- **5xx 를 반환하지 않는다.** 타임아웃·예외 시에도 200과 함께 사유가 담긴
  `think_trace` 와 안내 문구를 반환한다.
