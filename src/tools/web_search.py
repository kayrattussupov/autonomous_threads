import os
import re

import requests

from src.config import load_settings

TAVILY_URL = "https://api.tavily.com/search"


def web_search(query: str, max_results: int | None = None) -> list[dict]:
    """Only used for category='news' drafts (SPEC.md §6.1). Returns [] on
    any failure rather than raising — a failed search should not crash the
    content_agent's ReAct loop; the LLM sees an empty result and adapts."""
    max_results = max_results or load_settings()["search"]["max_results"]
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return []

    try:
        resp = requests.post(
            TAVILY_URL,
            json={"api_key": api_key, "query": query, "max_results": max_results},
            timeout=15,
        )
    except requests.RequestException:
        return []

    if resp.status_code != 200:
        return []

    data = resp.json()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
        for r in data.get("results", [])
    ]


def verify_source(url: str) -> bool:
    """SPEC.md §6.1: HTTP 200 + non-empty <title>."""
    try:
        resp = requests.get(url, timeout=10)
    except requests.RequestException:
        return False

    if resp.status_code != 200:
        return False

    match = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
    if not match:
        return False
    return bool(match.group(1).strip())
