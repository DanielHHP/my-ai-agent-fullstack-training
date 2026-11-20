"""Direct DashScope text generation for grounded RAG answers."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from .config import (
    DASHSCOPE_LLM_MAX_TOKENS,
    DASHSCOPE_LLM_MODEL,
    DASHSCOPE_LLM_TIMEOUT_SECONDS,
    dashscope_api_key,
)
from .errors import LLMError

SYSTEM_PROMPT = """你是严格的知识库 RAG 助手。
只能依据用户提供的检索证据回答，不得使用证据之外的知识。
每条事实性结论后必须使用 [编号] 标注来源；证据不足时直接回答“证据不足”。
"""

MULTIMODAL_MODEL_MARKERS = (
    "-vl",
    "-omni",
    "qwen3.7-plus",
    "qwen3.8-flash",
    "qwen3.8-max",
)


def _uses_multimodal_endpoint(model: str) -> bool:
    """Return whether DashScope routes this model through multimodal-generation."""
    normalized = model.lower()
    return any(marker in normalized for marker in MULTIMODAL_MODEL_MARKERS)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(part.strip() for part in parts if part.strip())
    return ""


def _choice_message_content(output: Any) -> str:
    if output is None:
        raise LLMError("DashScope returned no output.")
    if isinstance(output, dict):
        choices = output.get("choices")
    else:
        choices = getattr(output, "choices", None)
    if not choices:
        raise LLMError("DashScope returned no choices.")

    message = choices[0]
    if isinstance(message, dict):
        message = message.get("message")
    else:
        message = getattr(message, "message", None)
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    text = _content_text(content)
    if not text:
        raise LLMError("DashScope returned an empty answer.")
    return text


def generate_answer(prompt: str, model: str | None = None) -> str:
    """Generate one answer through DashScope's native text-generation API."""
    import dashscope

    resolved_model = (model or DASHSCOPE_LLM_MODEL).strip()
    if not resolved_model:
        raise LLMError("DashScope LLM model must not be empty.")

    if _uses_multimodal_endpoint(resolved_model):
        messages = [
            {"role": "system", "content": [{"text": SYSTEM_PROMPT}]},
            {"role": "user", "content": [{"text": prompt}]},
        ]
        response = dashscope.MultiModalConversation.call(
            model=resolved_model,
            messages=messages,
            api_key=dashscope_api_key(),
            result_format="message",
            temperature=0,
            max_tokens=DASHSCOPE_LLM_MAX_TOKENS,
            stream=False,
            request_timeout=DASHSCOPE_LLM_TIMEOUT_SECONDS,
        )
    else:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        response = dashscope.Generation.call(
            model=resolved_model,
            messages=messages,
            api_key=dashscope_api_key(),
            result_format="message",
            temperature=0,
            max_tokens=DASHSCOPE_LLM_MAX_TOKENS,
            stream=False,
            request_timeout=DASHSCOPE_LLM_TIMEOUT_SECONDS,
        )
    if response.status_code != HTTPStatus.OK:
        message = getattr(response, "message", "unknown error")
        raise LLMError(f"DashScope generation failed ({response.status_code}): {message}")
    return _choice_message_content(response.output)
