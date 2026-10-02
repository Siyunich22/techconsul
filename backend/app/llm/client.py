"""Единственная точка вызова Anthropic API (CLAUDE.md, правило 5).

Ретраи сети/429/5xx — в SDK (max_retries). Структурированный ответ валидируется Pydantic-схемой;
при невалидном ответе — один повтор с текстом ошибки в контексте, затем LLMOutputError (правило 7).
Каждый вызов записывается в llm_calls (токены, стоимость).
"""

import logging
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, Protocol

import anthropic
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.llm.cost import cost_usd
from app.models import LlmCall

log = logging.getLogger(__name__)

ModelRole = Literal["analysis", "extract", "cheap"]


class LLMError(Exception):
    pass


class LLMUnavailable(LLMError):
    """Ключ API не задан или сервис недоступен — вызывающий код использует запасной путь."""


class LLMRefusal(LLMError):
    pass


class LLMOutputError(LLMError):
    """Ответ не прошёл валидацию схемой и после повтора."""


@dataclass(frozen=True)
class CallContext:
    purpose: str
    org_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None


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
    ) -> T: ...


def model_for(role: ModelRole) -> str:
    s = get_settings()
    return {"analysis": s.llm_model_analysis, "extract": s.llm_model_extract, "cheap": s.llm_model_cheap}[
        role
    ]


class AnthropicLLM:
    def __init__(self, db_factory, client: anthropic.Anthropic | None = None) -> None:
        self._db_factory = db_factory
        self._client = client

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            key = get_settings().anthropic_api_key
            if not key:
                raise LLMUnavailable("ANTHROPIC_API_KEY не задан")
            self._client = anthropic.Anthropic(api_key=key, max_retries=4, timeout=600.0)
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
    ) -> T:
        model = model_for(role)
        messages: list[dict] = [{"role": "user", "content": user}]
        last_error: Exception | None = None
        for attempt in range(2):
            response = None
            try:
                response = self._call(ctx, model, system, messages, schema, max_tokens)
                if response.stop_reason == "refusal":
                    raise LLMRefusal(f"Модель отказалась отвечать ({ctx.purpose})")
                parsed = response.parsed_output
                if parsed is None:
                    raise LLMOutputError(
                        f"Пустой структурированный ответ, stop_reason={response.stop_reason}"
                    )
                return parsed
            except (ValidationError, LLMOutputError, ValueError) as exc:
                last_error = exc
                log.warning("Невалидный ответ LLM (%s), попытка %d: %s", ctx.purpose, attempt + 1, exc)
                retry_note = f"Ответ не прошёл проверку схемы: {exc}. Повтори ответ строго по схеме."
                if response is not None and _text(response):
                    messages = [*messages, {"role": "assistant", "content": _text(response)}]
                    messages = [*messages, {"role": "user", "content": retry_note}]
                else:
                    messages = [{"role": "user", "content": f"{user}\n\n{retry_note}"}]
        raise LLMOutputError(str(last_error))

    def _call(self, ctx, model, system, messages, schema, max_tokens):
        try:
            response = self.client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                output_format=schema,
            )
        except anthropic.APIConnectionError as exc:
            self._record(ctx, model, None, status="error", error=str(exc))
            raise LLMUnavailable(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            self._record(ctx, model, None, status="error", error=f"{exc.status_code}: {exc.message}")
            if exc.status_code >= 500 or exc.status_code in (401, 403, 429):
                raise LLMUnavailable(str(exc)) from exc
            raise LLMError(str(exc)) from exc
        self._record(ctx, model, response)
        return response

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
        try:
            with self._db_factory() as db:
                _store_call(db, ctx, model, tokens, status, error, getattr(response, "_request_id", None))
        except Exception:  # учёт не должен ронять основной вызов
            log.exception("Не удалось записать учёт вызова LLM")


def _store_call(db: Session, ctx, model, tokens, status, error, request_id) -> None:
    db.add(
        LlmCall(
            org_id=ctx.org_id,
            project_id=ctx.project_id,
            purpose=ctx.purpose,
            model=model,
            input_tokens=tokens["input_tokens"],
            output_tokens=tokens["output_tokens"],
            cache_read_tokens=tokens["cache_read"],
            cache_write_tokens=tokens["cache_write"],
            cost_usd=cost_usd(model, **tokens),
            status=status,
            error=error,
            request_id=request_id,
        )
    )
    db.commit()


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
