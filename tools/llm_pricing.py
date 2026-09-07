"""Central pricing policy for LLM calls whose provider does not report cost."""

OPENAI_TOKEN_RATES = {
    # USD micros per one million tokens.
    "gpt-4.1-mini": {"input_tokens": 400_000, "output_tokens": 1_600_000},
    "text-embedding-3-small": {"input_tokens": 20_000, "output_tokens": 0},
}


def estimate_openai_cost_micros(model, usage):
    try:
        rates = OPENAI_TOKEN_RATES[model]
    except KeyError as exc:
        raise ValueError(f"No approved pricing policy exists for model {model!r}.") from exc
    numerator = sum((usage.get(field) or 0) * rate for field, rate in rates.items())
    return (numerator + 999_999) // 1_000_000
