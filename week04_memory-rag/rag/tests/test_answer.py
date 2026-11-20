from __future__ import annotations

import json

import dashscope
import pytest

import rag.cli as cli
from rag.search import SearchHit, SearchResponse


def _response() -> SearchResponse:
    hit = SearchHit(
        rank=1,
        node_id="node-1",
        file="docs/session-rules.md",
        doc_type="doc",
        heading="登录会话有效期规则 > 有效期长度",
        score=0.25,
        score_type="rrf",
        text="管理后台的登录会话有效期为 30 分钟。",
    )
    return SearchResponse(
        question="登录会话的有效期是多少分钟？",
        mode="hybrid",
        top_k=5,
        candidate_k=20,
        result_count=1,
        results=[hit],
    )


def test_ask_no_llm_degrades_without_calling_model(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "search", lambda **_: _response())

    def fail_if_called(*args, **kwargs):
        raise AssertionError("LLM must not be called with --no-llm")

    monkeypatch.setattr("rag.answer.generate_answer", fail_if_called)

    assert cli.main(["ask", "问题", "--no-llm", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["answer"] is None
    assert payload["llm_error"] is None
    assert payload["degraded"] is True
    assert payload["degradation_reason"] == "llm_disabled"
    assert payload["citations"][0]["file"] == "docs/session-rules.md"


def test_ask_llm_failure_degrades_but_strict_mode_fails(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "search", lambda **_: _response())

    def fail_generation(*args, **kwargs):
        raise RuntimeError("simulated generation outage")

    monkeypatch.setattr("rag.answer.generate_answer", fail_generation)

    assert cli.main(["ask", "问题", "--json"]) == 0
    degraded = json.loads(capsys.readouterr().out)
    assert degraded["answer"] is None
    assert degraded["degraded"] is True
    assert degraded["degradation_reason"] == "llm_error"
    assert "simulated generation outage" in degraded["llm_error"]
    assert degraded["result_count"] == 1

    assert cli.main(["ask", "问题", "--strict-llm", "--json"]) == 1
    strict = json.loads(capsys.readouterr().out)
    assert strict["answer"] is None
    assert strict["degradation_reason"] == "llm_error"


def test_generate_answer_uses_dashscope_generation_api(monkeypatch) -> None:
    calls = []

    class Response:
        status_code = 200
        output = {
            "choices": [
                {"message": {"role": "assistant", "content": "登录会话有效期为 30 分钟。[1]"}}
            ]
        }

    def fake_call(**kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr(dashscope.Generation, "call", fake_call)
    monkeypatch.setattr("rag.llm.dashscope_api_key", lambda: "test-key")

    from rag.llm import generate_answer

    answer = generate_answer("测试提示词", model="qwen-test")

    assert answer == "登录会话有效期为 30 分钟。[1]"
    assert calls[0]["model"] == "qwen-test"
    assert calls[0]["api_key"] == "test-key"
    assert calls[0]["result_format"] == "message"
    assert calls[0]["messages"][0]["role"] == "system"
    assert calls[0]["messages"][1]["content"] == "测试提示词"


def test_generate_answer_uses_multimodal_api_for_multimodal_model(monkeypatch) -> None:
    calls = []

    class Response:
        status_code = 200
        output = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": [{"text": "登录会话有效期为 30 分钟。[1]"}],
                    }
                }
            ]
        }

    def fake_call(**kwargs):
        calls.append(kwargs)
        return Response()

    def fail_text_endpoint(**kwargs):
        raise AssertionError("multimodal models must not use Generation.call")

    monkeypatch.setattr(dashscope.Generation, "call", fail_text_endpoint)
    monkeypatch.setattr(dashscope.MultiModalConversation, "call", fake_call)
    monkeypatch.setattr("rag.llm.dashscope_api_key", lambda: "test-key")

    from rag.llm import SYSTEM_PROMPT, generate_answer

    answer = generate_answer("测试提示词", model="qwen3.8-flash")

    assert answer == "登录会话有效期为 30 分钟。[1]"
    assert calls[0]["model"] == "qwen3.8-flash"
    assert calls[0]["api_key"] == "test-key"
    assert calls[0]["result_format"] == "message"
    assert calls[0]["messages"][0]["content"] == [{"text": SYSTEM_PROMPT}]
    assert calls[0]["messages"][1]["content"] == [{"text": "测试提示词"}]


def test_generate_answer_rejects_empty_model(monkeypatch) -> None:
    from rag.llm import generate_answer

    with pytest.raises(Exception, match="model must not be empty"):
        generate_answer("测试提示词", model="  ")
