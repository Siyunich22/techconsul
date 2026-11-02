"""Единственная точка вызова Anthropic API (CLAUDE.md, правило 5).

Ретраи сети/429/5xx — в SDK (max_retries); обрыв потока посреди ответа — здесь (_call).
Структурированный ответ валидируется Pydantic-схемой;
при невалидном ответе — один повтор с текстом ошибки в контексте, затем LLMOutputError (правило 7).
Каждый вызов записывается в llm_calls (токены, стоимость).
"""

import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Literal, Protocol

import anthropic
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.llm.cost import cost_usd
from app.models import LlmCall

log = logging.getLogger(__name__)

ModelRole = Literal["analysis", "items", "extract", "cheap"]
WEB_SEARCH_USD = Decimal("0.01")  # серверный web search: $10 за 1000 запросов, сверх токенов
STREAM_RETRY_DELAYS_S = (5, 20)  # обрыв потока (overloaded посреди ответа) — SDK такой не повторяет


class LLMError(Exception):
    pass


class LLMUnavailable(LLMError):
    """Ключ API не задан или сервис недоступен — вызывающий код использует запасной путь."""


class LLMBillingError(LLMUnavailable):
    """На балансе Anthropic нет средств: следующие вызовы тоже не пройдут — прогон останавливается."""


BILLING_MESSAGE = (
    "Недостаточно средств на балансе Anthropic — пополните баланс (console.anthropic.com → Billing) "
    "и довыполните пункты"
)


class LLMRefusal(LLMError):
    pass


class LLMOutputError(LLMError):
    """Ответ не прошёл валидацию схемой и после повтора."""


@dataclass(frozen=True)
class CallContext:
    purpose: str
    org_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None


@dataclass(frozen=True)
class AgentTool:
    """Клиентский инструмент агента: описание для модели и исполнитель (возвращает текст результата)."""

    name: str
    description: str
    input_schema: dict
    run: Callable[[dict], str]

    def definition(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


class LLM(Protocol):
    def structured[T: BaseModel](
        self,
        *,
        ctx: CallContext,
        role: ModelRole,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = 4096,
        constrained: bool = True,
    ) -> T: ...

    def agent[T: BaseModel](
        self,
        *,
        ctx: CallContext,
        role: ModelRole,
        system: str,
        user: str,
        tools: list[AgentTool],
        server_tools: list[dict],
        schema: type[T],
        max_turns: int = 12,
        max_tokens: int = 32_000,
    ) -> T: ...


def model_for(role: ModelRole) -> str:
    s = get_settings()
    return {
        "analysis": s.llm_model_analysis,
        "items": s.llm_model_items,
        "extract": s.llm_model_extract,
        "cheap": s.llm_model_cheap,
    }[role]


class AnthropicLLM:
    def __init__(self, db_factory, client: anthropic.Anthropic | None = None) -> None:
        self._db_factory = db_factory
        self._client = client

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            s = get_settings()
            if not s.anthropic_api_key:
                raise LLMUnavailable("ANTHROPIC_API_KEY не задан")
            workspace = s.anthropic_workspace_id
            headers = {"anthropic-workspace-id": workspace} if workspace else None
            self._client = anthropic.Anthropic(
                api_key=s.anthropic_api_key, max_retries=4, timeout=600.0, default_headers=headers
            )
        return self._client

    def structured[T: BaseModel](
        self,
        *,
        ctx: CallContext,
        role: ModelRole,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = 4096,
        constrained: bool = True,
    ) -> T:
        """constrained=True — ответ ограничен грамматикой схемы (structured outputs). Для больших схем
        (паспорт) API отвечает «grammar is too large» — тогда constrained=False: схема в системном промпте,
        ответ — JSON в тексте, проверка Pydantic на нашей стороне и тот же один повтор (правило 7)."""
        model = model_for(role)
        if not constrained:
            system = f"{system}\n\n{_json_instruction(schema)}"
        messages: list[dict] = [{"role": "user", "content": user}]
        last_error: Exception | None = None
        for attempt in range(2):
            response = None
            try:
                response = self._call(
                    ctx, model, system, messages, schema if constrained else None, max_tokens
                )
                if response.stop_reason == "refusal":
                    raise LLMRefusal(f"Модель отказалась отвечать ({ctx.purpose})")
                if response.stop_reason == "max_tokens":
                    raise LLMOutputError(f"Ответ обрезан лимитом max_tokens={max_tokens}")
                parsed = response.parsed_output if constrained else _parse_json(_text(response), schema)
                if parsed is None:
                    raise LLMOutputError(
                        f"Пустой структурированный ответ, stop_reason={response.stop_reason}"
                    )
                return parsed
            except (ValidationError, LLMOutputError, ValueError) as exc:
                last_error = exc
                log.warning("Невалидный ответ LLM (%s), попытка %d: %s", ctx.purpose, attempt + 1, exc)
                retry_note = (
                    f"Ответ не прошёл проверку схемы: {str(exc)[:2000]}. Повтори ответ строго по схеме."
                )
                previous = _text(response) if response is not None else ""
                if previous:
                    retry_note = f"Твой предыдущий ответ:\n{previous[:20000]}\n\n{retry_note}"
                # одно новое сообщение пользователя, без правки истории (preserved thinking у новых моделей)
                messages = [{"role": "user", "content": f"{user}\n\n{retry_note}"}]
        raise LLMOutputError(str(last_error))

    def agent[T: BaseModel](
        self,
        *,
        ctx: CallContext,
        role: ModelRole,
        system: str,
        user: str,
        tools: list[AgentTool],
        server_tools: list[dict],
        schema: type[T],
        max_turns: int = 12,
        max_tokens: int = 32_000,
    ) -> T:
        """Цикл инструментов (TZ §5.3): модель вызывает инструменты, итог — JSON по schema.

        Клиентские инструменты исполняются здесь; серверные (web search/fetch) — на стороне Anthropic,
        `pause_turn` продолжается тем же запросом. История только дополняется (preserved thinking).
        После max_turns инструменты отключаются (tool_choice none) — модель обязана дать итог.
        Невалидный итог — один повтор с ошибкой (правило 7)."""
        model = model_for(role)
        system = f"{system}\n\n{_json_instruction(schema)}"
        by_name = {t.name: t for t in tools}
        definitions = [t.definition() for t in tools] + list(server_tools)
        messages: list[dict] = [{"role": "user", "content": user}]
        turns = continuations = 0
        retried = False
        while True:
            extra: dict = {"tools": definitions} if definitions else {}
            if definitions and (turns >= max_turns or retried):
                extra["tool_choice"] = {"type": "none"}
            # история растёт с каждым ходом: кэш-точка на её конце, иначе каждый ход оплачивается целиком
            response = self._call(
                ctx, model, system, _with_cache_breakpoint(messages), None, max_tokens, **extra
            )
            if response.stop_reason == "refusal":
                raise LLMRefusal(f"Модель отказалась отвечать ({ctx.purpose})")
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "pause_turn":
                continuations += 1
                if continuations > 5:
                    raise LLMOutputError("Серверные инструменты не завершились за 5 продолжений")
                continue
            calls = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            if response.stop_reason == "tool_use" and calls:
                turns += 1
                results = [_run_tool(by_name, call) for call in calls]
                if turns >= max_turns:
                    note = "Лимит обращений к инструментам исчерпан — дай итоговый ответ."
                    results.append({"type": "text", "text": note})
                messages.append({"role": "user", "content": results})
                continue
            if response.stop_reason == "max_tokens":
                # обрезанный ход может кончаться незавершённым tool_use — в истории он даст ошибку 400
                messages.pop()
                error: Exception = LLMOutputError(f"Ответ обрезан лимитом max_tokens={max_tokens}")
            else:
                try:
                    return _parse_json(_text(response), schema)
                except (ValidationError, LLMOutputError, ValueError) as exc:
                    error = exc
            log.warning("Невалидный итог агента (%s): %s", ctx.purpose, error)
            if retried:
                raise LLMOutputError(str(error))
            retried = True
            _append_user(
                messages,
                f"Итог не прошёл проверку схемы: {str(error)[:2000]}. "
                "Повтори итоговый ответ строго по схеме, без вызова инструментов.",
            )

    def _call(self, ctx, model, system, messages, schema, max_tokens, **extra):
        params = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
            **extra,
        }
        for attempt in range(len(STREAM_RETRY_DELAYS_S) + 1):
            try:
                if schema is None:
                    # длинные ответы (сведение паспорта) — потоком: без него SDK ограничивает max_tokens
                    with self.client.messages.stream(**params) as stream:
                        response = stream.get_final_message()
                else:
                    response = self.client.messages.parse(**params, output_format=schema)
            except anthropic.APIConnectionError as exc:
                self._record(ctx, model, None, status="error", error=str(exc))
                if attempt < len(STREAM_RETRY_DELAYS_S):
                    time.sleep(STREAM_RETRY_DELAYS_S[attempt])
                    continue
                raise LLMUnavailable(str(exc)) from exc
            except anthropic.APIStatusError as exc:
                self._record(ctx, model, None, status="error", error=f"{exc.status_code}: {exc.message}")
                if _transient(exc) and attempt < len(STREAM_RETRY_DELAYS_S):
                    time.sleep(STREAM_RETRY_DELAYS_S[attempt])
                    continue
                if "credit balance" in str(exc.message).lower():
                    raise LLMBillingError(BILLING_MESSAGE) from exc
                if exc.status_code >= 500 or exc.status_code in (401, 403, 429) or _transient(exc):
                    raise LLMUnavailable(str(exc)) from exc
                raise LLMError(str(exc)) from exc
            self._record(ctx, model, response)
            return response
        raise AssertionError("unreachable")

    def _record(
        self, ctx: CallContext, model: str, response, *, status: str = "ok", error: str | None = None
    ) -> None:
        usage = getattr(response, "usage", None)
        tokens = {
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        }
        server = getattr(usage, "server_tool_use", None)
        searches = getattr(server, "web_search_requests", 0) or 0
        if not isinstance(searches, int):
            searches = 0
        try:
            with self._db_factory() as db:
                _store_call(
                    db, ctx, model, tokens, status, error, getattr(response, "_request_id", None), searches
                )
        except Exception:  # учёт не должен ронять основной вызов
            log.exception("Не удалось записать учёт вызова LLM")


def _store_call(db: Session, ctx, model, tokens, status, error, request_id, searches: int = 0) -> None:
    db.add(
        LlmCall(
            org_id=ctx.org_id,
            project_id=ctx.project_id,
            run_id=ctx.run_id,
            purpose=ctx.purpose,
            model=model,
            input_tokens=tokens["input_tokens"],
            output_tokens=tokens["output_tokens"],
            cache_read_tokens=tokens["cache_read"],
            cache_write_tokens=tokens["cache_write"],
            cost_usd=cost_usd(model, **tokens) + WEB_SEARCH_USD * searches,
            status=status,
            error=error,
            request_id=request_id,
        )
    )
    db.commit()


def _run_tool(by_name: dict[str, AgentTool], call) -> dict:
    tool = by_name.get(call.name)
    try:
        if tool is None:
            raise ValueError(f"Инструмент {call.name} недоступен для этого пункта")
        return {"type": "tool_result", "tool_use_id": call.id, "content": tool.run(dict(call.input or {}))}
    except Exception as exc:  # ошибка инструмента — сообщение модели, а не падение пункта
        log.warning("Инструмент %s: %s", call.name, exc)
        return {"type": "tool_result", "tool_use_id": call.id, "content": f"Ошибка: {exc}", "is_error": True}


def _json_instruction(schema: type[BaseModel]) -> str:
    return (
        "Формат ответа: только один JSON-объект без пояснений и без markdown, строго по JSON Schema:\n"
        + json.dumps(schema.model_json_schema(), ensure_ascii=False)
    )


def _parse_json[T: BaseModel](text: str, schema: type[T]) -> T:
    """JSON-объект из текста ответа (допускаем обёртку markdown и поясняющий текст вокруг).

    Агент до итога может писать пояснения с фигурными скобками: берём все полные JSON-объекты
    текста и возвращаем последний, прошедший схему (итог — в конце)."""
    decoder = json.JSONDecoder()
    candidates: list[dict] = []
    i = text.find("{")
    while i >= 0:
        try:
            obj, end = decoder.raw_decode(text, i)
        except ValueError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict):
            candidates.append(obj)
        i = text.find("{", end)
    if not candidates:
        raise LLMOutputError("В ответе нет JSON-объекта")
    error: ValidationError | None = None
    for obj in reversed(candidates):
        try:
            return schema.model_validate(obj)
        except ValidationError as exc:
            error = error or exc
    raise error  # type: ignore[misc]


def _transient(exc: anthropic.APIStatusError) -> bool:
    """Перегрузка/внутренняя ошибка — в т.ч. событие error посреди потока (статус ответа там 200)."""
    if exc.status_code in (429, 500, 502, 503, 504, 529):
        return True
    body = exc.body if isinstance(exc.body, dict) else {}
    err = body.get("error")
    kind = err.get("type") if isinstance(err, dict) else body.get("type")
    return kind in ("overloaded_error", "api_error", "rate_limit_error")


def _with_cache_breakpoint(messages: list[dict]) -> list[dict]:
    """Копия истории с cache_control на последнем блоке последнего сообщения пользователя. Историю не
    меняем: старые точки накапливались бы сверх лимита API (4 на запрос)."""
    if not messages or messages[-1]["role"] != "user":
        return messages
    last = messages[-1]
    content = last["content"]
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
    if not blocks or not isinstance(blocks[-1], dict):
        return messages
    blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
    return [*messages[:-1], {**last, "content": blocks}]


def _append_user(messages: list[dict], text: str) -> None:
    """Текст пользователю: к последнему сообщению пользователя (после снятого обрезанного хода),
    иначе — новым сообщением; два сообщения пользователя подряд не отправляем."""
    if messages and messages[-1]["role"] == "user":
        content = messages[-1]["content"]
        if isinstance(content, str):
            messages[-1] = {"role": "user", "content": f"{content}\n\n{text}"}
        else:
            messages[-1] = {"role": "user", "content": [*content, {"type": "text", "text": text}]}
        return
    messages.append({"role": "user", "content": text})


def _text(response) -> str:
    return "".join(b.text for b in response.content if getattr(b, "type", None) == "text")


@lru_cache
def _default_llm() -> AnthropicLLM:
    from app.core.db import get_sessionmaker

    return AnthropicLLM(get_sessionmaker())


_override: LLM | None = None


def get_llm() -> LLM:
    return _override or _default_llm()


def set_llm_override(llm: LLM | None) -> None:
    """Для тестов: подмена клиента моком (тесты не ходят в реальный API — правило 9)."""
    global _override
    _override = llm
