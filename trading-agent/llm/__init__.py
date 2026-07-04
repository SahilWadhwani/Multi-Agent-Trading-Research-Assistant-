"""
LLM Client Module for QUANT-1

Supports multiple local LLM backends:
- Ollama (recommended - easiest setup)
- OpenAI-compatible APIs
- Direct HuggingFace transformers

Recommended models for trading analysis:
- DeepSeek-R1-Distill-Qwen-7B (best reasoning)
- Qwen2.5-7B-Instruct (fast, good quality)
- Llama-3.1-8B-Instruct (well-rounded)
"""

from .client import LLMClient, get_llm_client

__all__ = ["LLMClient", "get_llm_client"]
