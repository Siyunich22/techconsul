"""LLM-клиент на моке SDK Anthropic: повтор при невалидном ответе, отказ, учёт токенов и стоимости."""

from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from app.llm.client import AnthropicLLM, CallContext, LLMOutputError, LLMRefusal
from app.llm.cost import cost_usd
from app.llm.prompts import render


class Answer(BaseModel):
    category: str


def _response(parsed, stop_reason="end_turn", text="{}"):
    usage = SimpleNamespace(
        input_tokens=1000, output_tokens=50, cache_read_input_tokens=200, cache_creation_input_tokens=0
    )
    return SimpleNamespace(
        parsed_output=parsed,
        stop_reason=stop_reason,
        usage=usage,
        _request_id="req_1",
        content=[SimpleNamespace(type="text", text=text)],
    )


class FakeMessages:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def parse(self, **kwargs):
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Recorder:
    def __init__(self):
        self.rows = []

    @contextmanager
    def __call__(self):
        rec = self

        class DB:
            def add(self, row):
                rec.rows.append(row)

            def commit(self):
                pass

        yield DB()


def _llm(outcomes):
    messages = FakeMessages(outcomes)
    recorder = Recorder()
    return AnthropicLLM(recorder, client=SimpleNamespace(messages=messages)), messages, recorder


def _call(llm):
    return llm.structured(ctx=CallContext(purpose="test"), role="cheap", system="S", user="U", schema=Answer)


def _validation_error():
    try:
        Answer.model_validate({})
    except ValidationError as exc:
        return exc


def test_valid_answer_and_accounting():
    llm, messages, rec = _llm([_response(Answer(category="тэо_bfs"))])
    assert _call(llm).category == "тэо_bfs"
    req = messages.requests[0]
    assert req["model"] == "claude-haiku-4-5-20251001" and req["output_format"] is Answer
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}  # prompt caching системного промпта
    row = rec.rows[0]
    assert (row.input_tokens, row.output_tokens, row.cache_read_tokens) == (1000, 50, 200)
    assert row.cost_usd == cost_usd("claude-haiku-4-5", input_tokens=1000, output_tokens=50, cache_read=200)


def test_invalid_output_retried_once_with_error_in_context():
    llm, messages, _ = _llm([_validation_error(), _response(Answer(category="псд"))])
    assert _call(llm).category == "псд"
    assert len(messages.requests) == 2
    assert "не прошёл проверку схемы" in messages.requests[1]["messages"][-1]["content"]


def test_invalid_output_twice_raises():
    llm, _, _ = _llm([_response(None), _response(None)])
    with pytest.raises(LLMOutputError):
        _call(llm)


def test_refusal_is_not_retried():
    llm, messages, _ = _llm([_response(None, stop_reason="refusal")])
    with pytest.raises(LLMRefusal):
        _call(llm)
    assert len(messages.requests) == 1


def test_cost_table():
    assert cost_usd("claude-opus-5-5", input_tokens=1_000_000, output_tokens=0) == Decimal("4.000000")
    assert cost_usd("claude-sonnet-5-5", input_tokens=0, output_tokens=1_000_000) == Decimal("10.000000")
    assert cost_usd(
        "claude-haiku-4-5-20251001", input_tokens=0, output_tokens=0, cache_write=1_000_000
    ) == Decimal("1.250000")
    assert cost_usd("unknown-model", input_tokens=10, output_tokens=10) == 0


def test_prompt_render_requires_all_variables():
    assert "- тэо_bfs" in render("classify_document", categories="- тэо_bfs — ТЭО")
    with pytest.raises(KeyError):
        render("classify_document")
