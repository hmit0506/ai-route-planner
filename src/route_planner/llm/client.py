"""
LLM client: DeepSeek primary (OpenAI-compatible) + Claude fallback.
"""
import json
import os
import re
import time
from typing import List, Dict, Any

from openai import OpenAI, RateLimitError as OpenAIRateLimitError, APIError as OpenAIAPIError
from anthropic import Anthropic


def _deepseek_client() -> OpenAI:
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set DEEPSEEK_API_KEY (or OPENAI_API_KEY) in your .env")
    return OpenAI(api_key=api_key, base_url="https://api.deepseek.com")


def _claude_client() -> Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Set ANTHROPIC_API_KEY in your .env for Claude fallback")
    return Anthropic(api_key=api_key)


def _extract_json(text: str) -> Any:
    """Strip markdown fences and parse JSON."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ```
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text.strip())


def call_llm(
    messages: List[Dict[str, str]],
    model: str = "deepseek-chat",
    retries: int = 3,
    parse_json: bool = False,
) -> str:
    """
    Call DeepSeek with exponential-backoff retry, then fall back to Claude.

    Returns the raw content string (or parsed dict if parse_json=True).
    """
    last_exc = None
    for attempt in range(retries):
        try:
            client = _deepseek_client()
            resp = client.chat.completions.create(model=model, messages=messages)
            content = resp.choices[0].message.content
            return _extract_json(content) if parse_json else content
        except (OpenAIRateLimitError, OpenAIAPIError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue

    # All DeepSeek retries exhausted — use Claude
    print(f"[LLM] DeepSeek failed ({last_exc}), falling back to Claude")
    claude = _claude_client()
    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=messages,
    )
    content = resp.content[0].text
    return _extract_json(content) if parse_json else content
