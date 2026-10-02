"""Стоимость вызовов LLM, USD за 1M токенов (прайс Anthropic API на 2026-09)."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Price:
    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal  # запись в кэш (TTL 5 мин) — 1.25× input


def _p(inp: str, out: str, cache_read: str) -> Price:
    i = Decimal(inp)
    return Price(
        input=i, output=Decimal(out), cache_read=Decimal(cache_read), cache_write=i * Decimal("1.25")
    )


PRICES: dict[str, Price] = {
    "claude-opus-5-5": _p("4", "20", "0.20"),
    "claude-sonnet-5-5": _p("2", "10", "0.20"),
    "claude-haiku-4-5": _p("1", "5", "0.10"),
}


def price_for(model: str) -> Price | None:
    """Модель может быть с датой версии (claude-haiku-4-5-20251001) — ищем по префиксу."""
    for name, price in PRICES.items():
        if model == name or model.startswith(name + "-"):
            return price
    return None


def cost_usd(
    model: str, *, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0
) -> Decimal:
    price = price_for(model)
    if price is None:
        return Decimal(0)
    total = (
        price.input * input_tokens
        + price.output * output_tokens
        + price.cache_read * cache_read
        + price.cache_write * cache_write
    )
    return (total / Decimal(1_000_000)).quantize(Decimal("0.000001"))
