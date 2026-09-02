import re

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?```\s*$", re.IGNORECASE | re.DOTALL)


def extract_json(text: str) -> str:
    """Best-effort strip of a markdown code fence around a JSON payload.

    LLMs (this project has no JSON response-format mode configured) routinely
    wrap JSON responses in ```json ... ``` fences. Callers should pass the
    result of this straight into json.loads(); if text has no fence, it is
    returned unchanged (json.loads will raise as before on genuinely invalid
    input).
    """
    if text is None:
        return text

    stripped = text.strip()
    match = _FENCE_RE.match(stripped)
    if match:
        return match.group(1).strip()

    # No full-string fence match (e.g. leading prose before the fence, or no
    # fence at all). Fall back to extracting the first balanced {...} object,
    # which handles both unfenced text-plus-JSON and odd fence placement.
    start = stripped.find("{")
    if start == -1:
        return stripped

    depth = 0
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : i + 1]

    return stripped
