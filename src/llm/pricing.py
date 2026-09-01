# USD per 1M tokens. Source: SPEC.md §5, verified 2026-09-01.
PRICE_TABLE = {
    "glm-4.7-flash":  {"input": 0.0,  "cached_input": 0.0,  "output": 0.0},
    "glm-4.7-flashx": {"input": 0.07, "cached_input": 0.01, "output": 0.40},
    "glm-4.5-air":    {"input": 0.20, "cached_input": 0.03, "output": 1.10},
    "glm-4.7":        {"input": 0.60, "cached_input": 0.11, "output": 2.20},
    "glm-5.3":        {"input": 1.40, "cached_input": 0.26, "output": 4.40},
    "kimi-k2.5":      {"input": 0.60, "cached_input": 0.10, "output": 3.00},
    "kimi-k2.6":      {"input": 0.95, "cached_input": 0.16, "output": 4.00},
}


def cost_usd(model: str, tokens_in: int, tokens_out: int, tokens_cached: int = 0) -> float:
    if model not in PRICE_TABLE:
        raise KeyError(f"no price entry for model {model!r} — add it to PRICE_TABLE")
    rates = PRICE_TABLE[model]
    billable_input = max(tokens_in - tokens_cached, 0)
    return (
        billable_input * rates["input"]
        + tokens_cached * rates["cached_input"]
        + tokens_out * rates["output"]
    ) / 1_000_000
