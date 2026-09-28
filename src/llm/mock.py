# -*- coding: utf-8 -*-
"""규칙 기반 스텁. 검색된 근거를 그대로 요약 인용해 반환한다.
목적: LLM 없이 라우팅·검색·계산·검증·가드레일 전 경로를 회귀 테스트하는 것.
"""
import re
from typing import List, Dict, Optional
from .base import LLMClient

class MockLLM(LLMClient):
    name = "mock"
    def chat(self, messages: List[Dict[str, str]], *, temperature: float = 0.1,
             max_tokens: int = 1024, stop: Optional[List[str]] = None) -> str:
        user = "\n".join(m["content"] for m in messages if m["role"] == "user")
        ctx = re.search(r"\[근거\](.*?)(?:\[질의\]|$)", user, re.S)
        body = (ctx.group(1).strip() if ctx else "")[:600]
        return f"[MOCK] 아래 근거를 바탕으로 답변합니다.\n{body}"
