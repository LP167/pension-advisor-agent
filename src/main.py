# -*- coding: utf-8 -*-
import os, sys, json, asyncio, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from schema import AnswerResponse
from pipeline import Agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = FastAPI(title="연금 Agent — 제10회 미래에셋증권 AI Festival", version="1.0")
AGENT: Agent = None
TIMEOUT = float(os.getenv("ANSWER_TIMEOUT", "40"))

@app.on_event("startup")
def _startup():
    global AGENT
    AGENT = Agent()
    logging.info("agent ready | llm=%s | chunks=%d", AGENT.llm.name, len(AGENT.idx.docs))

@app.get("/health")
def health():
    return {"status": "ok", "llm": AGENT.llm.name if AGENT else None,
            "chunks": len(AGENT.idx.docs) if AGENT else 0}

@app.get("/answer", response_model=AnswerResponse)
async def answer(question_id: str = Query(...), question: str = Query(...)):
    try:
        res = await asyncio.wait_for(asyncio.to_thread(AGENT.answer, question_id, question), timeout=TIMEOUT)
    except asyncio.TimeoutError:
        res = {"question_id": question_id, "question": question, "retrieved_context": "",
               "think_trace": json.dumps({"stop": "timeout"}, ensure_ascii=False),
               "answer": "처리 시간이 초과되어 부분 응답을 반환합니다. 질문을 나누어 다시 문의해 주십시오."}
    except Exception as e:
        logging.exception("answer failed")
        res = {"question_id": question_id, "question": question, "retrieved_context": "",
               "think_trace": json.dumps({"stop": "error", "type": type(e).__name__}, ensure_ascii=False),
               "answer": "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주십시오."}
    return JSONResponse(res)
