"""Grounded answer construction with automatic retrieval-only degradation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .config import DASHSCOPE_LLM_MODEL
from .llm import generate_answer
from .search import SearchHit, SearchResponse

DegradationReason = Literal["llm_disabled", "no_evidence", "llm_error"]


@dataclass(frozen=True)
class AnswerResponse:
    retrieval: SearchResponse
    answer: str | None
    llm_error: str | None
    llm_model: str
    degraded: bool
    degradation_reason: DegradationReason | None

    def to_dict(self, snippet_chars: int = 300) -> dict:
        payload = self.retrieval.to_dict(snippet_chars=snippet_chars)
        payload.update(
            {
                "answer": self.answer,
                "llm_error": self.llm_error,
                "llm_model": self.llm_model,
                "degraded": self.degraded,
                "degradation_reason": self.degradation_reason,
                "citations": [
                    {
                        "index": hit.rank,
                        "node_id": hit.node_id,
                        "file": hit.file,
                        "heading": hit.heading,
                        "score": round(hit.score, 6),
                        "score_type": hit.score_type,
                        "snippet": hit.text[:snippet_chars],
                    }
                    for hit in self.retrieval.results
                ],
            }
        )
        return payload


def _label(hit: SearchHit) -> str:
    return f"{hit.file} § {hit.heading}"


def assemble_evidence(hits: list[SearchHit]) -> str:
    """Render retrieved chunks as numbered, source-labelled evidence."""
    return "\n\n".join(
        f"[{hit.rank}] 来源：{_label(hit)}\n{hit.text.strip()}" for hit in hits
    )


def build_prompt(question: str, hits: list[SearchHit]) -> str:
    return f"""检索证据：
{assemble_evidence(hits)}

问题：{question}
"""


def _error_text(exc: Exception) -> str:
    message = str(exc).strip() or "unknown error"
    return f"{type(exc).__name__}: {message}"


def answer_question(
    retrieval: SearchResponse,
    *,
    allow_llm: bool = True,
    model: str | None = None,
) -> AnswerResponse:
    """Answer from retrieved evidence, retaining evidence on every failure path."""
    resolved_model = model or DASHSCOPE_LLM_MODEL

    if not retrieval.results:
        return AnswerResponse(
            retrieval=retrieval,
            answer=None,
            llm_error=None,
            llm_model=resolved_model,
            degraded=True,
            degradation_reason="no_evidence",
        )
    if not allow_llm:
        return AnswerResponse(
            retrieval=retrieval,
            answer=None,
            llm_error=None,
            llm_model=resolved_model,
            degraded=True,
            degradation_reason="llm_disabled",
        )

    try:
        answer = generate_answer(
            build_prompt(retrieval.question, retrieval.results),
            model=resolved_model,
        )
    except Exception as exc:
        return AnswerResponse(
            retrieval=retrieval,
            answer=None,
            llm_error=_error_text(exc),
            llm_model=resolved_model,
            degraded=True,
            degradation_reason="llm_error",
        )

    return AnswerResponse(
        retrieval=retrieval,
        answer=answer,
        llm_error=None,
        llm_model=resolved_model,
        degraded=False,
        degradation_reason=None,
    )
