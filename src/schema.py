# -*- coding: utf-8 -*-
"""평가용 API 응답 스키마 — 주최측 규격 4필드 고정."""
from typing import Optional
from pydantic import BaseModel, Field

class AnswerResponse(BaseModel):
    question_id: str
    question: str
    retrieved_context: str = Field("", description="답변 생성에 참고한 검색 문서")
    think_trace: str = Field("", description="사고·추론·도구 사용 과정 (검증 증명서 JSON 직렬화)")
    answer: str = ""

QUERY_TYPES = ["제도", "세제", "종합", "기타절차", "상품설명비교", "조건부추천"]
