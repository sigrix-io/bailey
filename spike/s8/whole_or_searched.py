"""Documents whole in the prompt or searched: what one message costs each way, at a few collection sizes.

Story S8 sets ``bailey.knowledge.WHOLE_LIMIT`` from this, as S1's
ruling asked. It prices one message both ways from the price data PydanticAI ships with
(``genai_prices``), on the two providers whose caches keep a prompt's stable start, with the
cache warm and with it gone cold, as it does after five idle minutes:

- **Whole:** one request. The instructions and the documents are the cached prefix; the
  conversation so far and the message are new.
- **Searched:** two requests. The first calls ``search_knowledge``; the second answers from
  the five passages it returned.

It prices tokens, not answers: whether a model answers as well either way is the live run's
to show. The sizes below are assumptions, from the fixture solution and a conversation a few
messages long.

    PYTHONPATH=src python spike/s8/whole_or_searched.py
"""

from __future__ import annotations

from decimal import Decimal

from genai_prices import calc_price
from pydantic_ai.usage import RunUsage

#: The instructions with the voice and the skill list, the conversation so far, the
#: message, and the answer, in tokens.
INSTRUCTIONS, CONVERSATION, MESSAGE, ANSWER = 1_200, 1_500, 100, 300
#: The search call, and the five passages it returns with their sources.
CALL, RESULTS = 40, 5 * 250
MODELS = (("claude-sonnet-5-5", "anthropic"), ("gpt-5-mini", "openai"))
SIZES = (2_000, 5_000, 10_000, 20_000, 30_000)


def cost(model: str, provider: str, **usage: int) -> Decimal:
    return Decimal(calc_price(RunUsage(**usage), model, provider_id=provider).total_price)  # type: ignore[arg-type]


def whole(model: str, provider: str, documents: int, *, warm: bool) -> Decimal:
    prefix = INSTRUCTIONS + documents
    cached = {"cache_read_tokens": prefix} if warm else _written(provider, prefix)
    return cost(model, provider, input_tokens=prefix + CONVERSATION + MESSAGE, output_tokens=ANSWER, **cached)


def searched(model: str, provider: str, *, warm: bool) -> Decimal:
    asked = INSTRUCTIONS + CONVERSATION + MESSAGE
    first = cost(
        model,
        provider,
        input_tokens=asked,
        output_tokens=CALL,
        **({"cache_read_tokens": INSTRUCTIONS} if warm else _written(provider, INSTRUCTIONS)),
    )
    # The second request repeats the first, which the provider has just cached.
    second = cost(
        model,
        provider,
        input_tokens=asked + CALL + RESULTS,
        output_tokens=ANSWER,
        cache_read_tokens=asked if warm else INSTRUCTIONS,
    )
    return first + second


def _written(provider: str, tokens: int) -> dict[str, int]:
    """A cold prefix: Anthropic charges for writing it to the cache, OpenAI reads it at the full price."""
    return {"cache_write_tokens": tokens} if provider == "anthropic" else {}


def main() -> None:
    for model, provider in MODELS:
        warm, cold = searched(model, provider, warm=True), searched(model, provider, warm=False)
        print(f"\n{model}: a searched message costs ${warm:.5f} with the cache warm, ${cold:.5f} cold")
        print("| Documents, tokens | Whole, warm | Whole against searched | Whole, cold | Whole against searched |")
        print("| ---: | ---: | ---: | ---: | ---: |")
        for size in SIZES:
            hot, chilled = whole(model, provider, size, warm=True), whole(model, provider, size, warm=False)
            print(f"| {size:,} | ${hot:.5f} | {hot / warm:.2f}x | ${chilled:.5f} | {chilled / cold:.2f}x |")


if __name__ == "__main__":
    main()
