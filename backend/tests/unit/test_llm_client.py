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


def test_unconstrained_json_mode_validates_and_retries():
    """Большие схемы: без грамматики structured outputs — схема в промпте, JSON проверяется у нас."""
    messages = FakeMessages([])

    @contextmanager
    def stream(**kwargs):  # ответ потоком: тот же учёт запросов, итог — get_final_message()
        response = messages.parse(**kwargs)
        yield SimpleNamespace(get_final_message=lambda: response)

    messages.stream = stream
    messages.outcomes = [
        _response(None, text='Вот ответ: {"wrong": 1}'),
        _response(None, text='```json\n{"category": "смета"}\n```'),
    ]
    llm = AnthropicLLM(Recorder(), client=SimpleNamespace(messages=messages))
    answer = llm.structured(
        ctx=CallContext(purpose="test"),
        role="extract",
        system="S",
        user="U",
        schema=Answer,
        constrained=False,
    )
    assert answer.category == "смета"
    first, retry = messages.requests
    assert "output_format" not in first and '"category"' in first["system"][0]["text"]  # схема в промпте
    assert retry["messages"] == [
        {"role": "user", "content": retry["messages"][0]["content"]}
    ]  # без правки истории
    assert '{"wrong": 1}' in retry["messages"][0]["content"]


def test_truncated_answer_is_output_error():
    llm, _, _ = _llm([_response(None, stop_reason="max_tokens"), _response(None, stop_reason="max_tokens")])
    with pytest.raises(LLMOutputError, match="max_tokens"):
        _call(llm)


def _blocks_response(blocks, stop_reason):
    usage = SimpleNamespace(
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0
    )
    return SimpleNamespace(stop_reason=stop_reason, usage=usage, _request_id="r", content=blocks)


def _tool_call(name, input_, id_="t1"):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def test_agent_loop_tools_limit_and_retry():
    from app.llm.client import AgentTool

    seen = []
    calc = AgentTool("calc", "d", {"type": "object"}, lambda a: seen.append(a) or "4")
    broken = AgentTool("broken", "d", {"type": "object"}, lambda a: 1 / 0)
    messages = FakeMessages(
        [
            _blocks_response(
                [_tool_call("calc", {"expression": "2+2"}), _tool_call("broken", {}, "t2")], "tool_use"
            ),
            _blocks_response(
                [_tool_call("calc", {"expression": "3"}, "t3")], "tool_use"
            ),  # лимит 2 обращения
            _blocks_response([SimpleNamespace(type="text", text="не JSON")], "end_turn"),
            _blocks_response([SimpleNamespace(type="text", text='{"category": "смета"}')], "end_turn"),
        ]
    )

    @contextmanager
    def stream(**kwargs):
        response = messages.parse(**kwargs | {"messages": list(kwargs["messages"])})  # снимок истории
        yield SimpleNamespace(get_final_message=lambda: response)

    messages.stream = stream
    rec = Recorder()
    llm = AnthropicLLM(rec, client=SimpleNamespace(messages=messages))
    answer = llm.agent(
        ctx=CallContext(purpose="assess:3.4.1"),
        role="analysis",
        system="S",
        user="U",
        tools=[calc, broken],
        server_tools=[{"type": "web_search_20260209", "name": "web_search"}],
        schema=Answer,
        max_turns=2,
    )
    assert answer.category == "смета" and seen == [{"expression": "2+2"}, {"expression": "3"}]
    first, second, third, fourth = messages.requests
    assert [t["name"] for t in first["tools"]] == [
        "calc",
        "broken",
        "web_search",
    ] and "tool_choice" not in first
    results = second["messages"][2]["content"]
    assert results[0] == {"type": "tool_result", "tool_use_id": "t1", "content": "4"}
    assert (
        results[1]["is_error"] and "ZeroDivisionError" not in results[1]["content"]
    )  # текст ошибки, не трейс
    assert third["tool_choice"] == {"type": "none"}  # после лимита — только итог
    assert "Лимит обращений" in third["messages"][-1]["content"][-1]["text"]
    retry_block = fourth["messages"][-1]["content"][-1]
    assert "не прошёл проверку" in retry_block["text"]
    assert retry_block["cache_control"] == {"type": "ephemeral"}  # кэш-точка на конце истории
    # кэш-точка только в отправленной копии: в истории предыдущих запросов её нет
    assert all("cache_control" not in b for b in fourth["messages"][2]["content"] if isinstance(b, dict))
    assert len(rec.rows) == 4  # каждый вызов учтён


def test_parse_json_takes_last_valid_object():
    from app.llm.client import _parse_json

    text = 'Ищу {в документах}. Нашёл {"x": 1}. Итог:\n```json\n{"category": "смета"}\n```'
    assert _parse_json(text, Answer).category == "смета"
    with pytest.raises(LLMOutputError):
        _parse_json("без JSON", Answer)


def test_stream_overloaded_midway_is_retried(monkeypatch):
    import httpx

    import app.llm.client as client_mod

    monkeypatch.setattr(client_mod, "STREAM_RETRY_DELAYS_S", (0, 0))
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    overloaded = client_mod.anthropic.APIStatusError(
        "Overloaded",
        response=httpx.Response(200, request=request),
        body={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    )
    messages = FakeMessages(
        [overloaded, _blocks_response([SimpleNamespace(type="text", text='{"category": "псд"}')], "end_turn")]
    )

    @contextmanager
    def stream(**kwargs):
        response = messages.parse(**kwargs)
        yield SimpleNamespace(get_final_message=lambda: response)

    messages.stream = stream
    rec = Recorder()
    llm = AnthropicLLM(rec, client=SimpleNamespace(messages=messages))
    answer = llm.agent(
        ctx=CallContext(purpose="t"),
        role="analysis",
        system="S",
        user="U",
        tools=[],
        server_tools=[],
        schema=Answer,
    )
    assert answer.category == "псд" and len(messages.requests) == 2
    assert [r.status for r in rec.rows] == ["error", "ok"]


def test_truncated_agent_turn_not_kept_in_history():
    messages = FakeMessages(
        [
            _blocks_response([_tool_call("calc", {"expression": "1"})], "max_tokens"),
            _blocks_response([SimpleNamespace(type="text", text='{"category": "смета"}')], "end_turn"),
        ]
    )

    @contextmanager
    def stream(**kwargs):
        response = messages.parse(**kwargs | {"messages": list(kwargs["messages"])})
        yield SimpleNamespace(get_final_message=lambda: response)

    messages.stream = stream
    llm = AnthropicLLM(Recorder(), client=SimpleNamespace(messages=messages))
    llm.agent(
        ctx=CallContext(purpose="t"),
        role="analysis",
        system="S",
        user="U",
        tools=[],
        server_tools=[],
        schema=Answer,
    )
    retry = messages.requests[1]["messages"]
    assert [m["role"] for m in retry] == ["user"]  # обрезанный ход с незавершённым tool_use снят
    assert "max_tokens" in retry[0]["content"][-1]["text"]


def test_web_search_requests_are_billed():
    response = _response(Answer(category="x"))
    response.usage.server_tool_use = SimpleNamespace(web_search_requests=3)
    llm, _, rec = _llm([response])
    _call(llm)
    base = cost_usd("claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=50, cache_read=200)
    assert rec.rows[0].cost_usd == base + Decimal("0.03")
