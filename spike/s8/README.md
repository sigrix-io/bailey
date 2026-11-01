# S8: documents whole or searched

The measurement behind `bailey.knowledge.WHOLE_LIMIT`, for
[sigrix-io/sigrix#6566](https://github.com/sigrix-io/sigrix/issues/6566). S1's ruling sent a
collection under a size limit into the prompt whole, where prompt caching keeps its repeat cost
low, and left this story to set the limit by measurement. None of it ships.

`whole_or_searched.py` prices one message both ways from the price data PydanticAI ships with
(`genai-prices` 0.2.1, under PydanticAI 2.56.0), with the cache warm and with it gone cold, as it
does after a few idle minutes. Whole is one request whose cached prefix is the instructions and the
documents. Searched is two: one that calls `search_knowledge`, one that answers from its five
passages. It prices tokens, not answers; whether a model answers as well either way is the live
run's to show.

```sh
PYTHONPATH=src python spike/s8/whole_or_searched.py
```

What it printed on 2026-11-01, as whole against searched:

| Documents, tokens | Sonnet 5.5, warm | Sonnet 5.5, cold | gpt-5-mini, warm | gpt-5-mini, cold |
| ---: | ---: | ---: | ---: | ---: |
| 2,000 | 0.68x | 0.92x | 0.72x | 0.84x |
| 5,000 | 0.71x | 1.40x | 0.77x | 1.20x |
| 10,000 | 0.76x | 2.21x | 0.85x | 1.78x |
| 20,000 | 0.87x | 3.82x | 1.02x | 2.95x |
| 30,000 | 0.97x | 5.43x | 1.18x | 4.13x |

A searched message cost $0.00958 on Sonnet 5.5 with the cache warm and $0.01550 cold;
$0.00150 and $0.00213 on gpt-5-mini.

**The limit is 10,000 tokens.** There, whole is cheaper on both providers while the cache is
warm, which it is through a conversation, and the model reads every passage, so no search can
miss one. The first message after a pause costs two cents more on Sonnet. At 20,000 the warm
saving is gone on OpenAI and a cold message costs nearly four times a searched one on Anthropic.

A local server's context window is not Bailey's to know, and local servers often open a model
with 4,096 tokens unless told otherwise. So a local server takes a collection whole only up to
2,000 tokens (`LOCAL_WHOLE_LIMIT`): that still fits beside the instructions, the latest message
and its answer, it spares a small model a tool call, and it needs no model download.
