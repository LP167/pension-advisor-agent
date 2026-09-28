# -*- coding: utf-8 -*-
"""LLM 어댑터. 규정상 최종 답변 생성은 HyperCLOVA X만 사용한다.
Mock-First: API Key 발급 전에도 파이프라인 전체를 개발·테스트할 수 있게 한다.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Optional

class LLMClient(ABC):
    name = "base"
    @abstractmethod
    def chat(self, messages: List[Dict[str, str]], *, temperature: float = 0.1,
             max_tokens: int = 1024, stop: Optional[List[str]] = None) -> str: ...

def get_llm() -> "LLMClient":
    import os
    if os.getenv("CLOVA_API_KEY"):
        from .clova import ClovaLLM
        return ClovaLLM()
    from .mock import MockLLM
    return MockLLM()
