FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Asia/Seoul
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY data/index/  ./data/index/
COPY data/master/ ./data/master/
COPY data/golden_set.json data/rule_tables.json ./data/

EXPOSE 8000

# ── 실측 기반 설정 (2026.08.31) ───────────────────────────────────────
# BM25 인덱스(82MB pickle, 청크 10,965 / term 158,625) 로드: 프로세스당 RSS 771MB.
#
# 기동 시간 — 08.31 측정 38.5초, 09.01 재측정 0.9~1.0초(3회). 38.5초는 재현되지 않았다.
#   페이지 캐시가 식은 상태였거나 다른 초기화가 섞인 값으로 보인다. 어느 쪽이든
#   느린 쪽을 기준으로 잡는다 — 컨테이너 첫 기동은 이미지 레이어에서 읽으므로
#   로컬 NVMe 보다 느리고, 클라우드 디스크는 더 느리다.
# start-period: 기존 30초는 38.5초 관측치보다 짧았다. 헬스체크가 준비 전에 실패 판정하고
#   restart:always 와 맞물리면 무한 재시작 루프가 된다. 여유를 크게 잡아 120초.
#   길게 잡아서 잃는 것은 첫 헬스체크가 늦어지는 것뿐이고, 짧게 잡으면 24일이 날아간다.
# workers=1: 워커는 각자 인덱스를 통째로 로드한다(771MB × N). 평가는 단발 GET이라
#   동시성 요구가 낮으므로 메모리를 지키는 쪽이 옳다. 동시 처리는 main.py 의
#   asyncio.to_thread 가 담당한다.
# timeout-keep-alive: 주최측 호출 간격을 모르므로 유휴 연결을 오래 잡지 않는다.
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=8).status==200 else 1)"

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--timeout-keep-alive", "15", "--limit-concurrency", "32"]
