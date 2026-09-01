import hashlib
import json
import os
import time
from dataclasses import dataclass

import yaml
from openai import OpenAI

from src.db.engine import session_scope
from src.db.repo import get_month_to_date_cost_usd, record_llm_call, upsert_daily_spend
from src.llm.pricing import cost_usd

BUDGET_SOFT_STOP_USD = 8.0
BUDGET_HARD_STOP_USD = 10.0
BUDGET_SOFT_STOP_ALLOWED_ROLE = "post_writer"


class BudgetExceeded(Exception):
    pass


@dataclass
class LLMResponse:
    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    model: str
    finish_reason: str | None


class LLMClient:
    def __init__(self, config_path: str = "config/models.yaml"):
        with open(config_path, encoding="utf-8") as f:
            self._config = yaml.safe_load(f)
        self._clients: dict[str, OpenAI] = {}

    def _client_for(self, provider: str) -> OpenAI:
        if provider not in self._clients:
            pconf = self._config["providers"][provider]
            api_key = os.environ[pconf["key_env"]]
            self._clients[provider] = OpenAI(base_url=pconf["base_url"], api_key=api_key)
        return self._clients[provider]

    def _check_budget(self, role: str) -> None:
        with session_scope() as session:
            spent = get_month_to_date_cost_usd(session)
        if spent >= BUDGET_HARD_STOP_USD:
            raise BudgetExceeded(f"month-to-date spend ${spent:.2f} >= hard stop ${BUDGET_HARD_STOP_USD}")
        if spent >= BUDGET_SOFT_STOP_USD and role != BUDGET_SOFT_STOP_ALLOWED_ROLE:
            raise BudgetExceeded(
                f"month-to-date spend ${spent:.2f} >= soft stop ${BUDGET_SOFT_STOP_USD}; "
                f"only role={BUDGET_SOFT_STOP_ALLOWED_ROLE!r} may still call"
            )

    def complete(
        self,
        role: str,
        messages: list[dict],
        run_id: int | None = None,
        step_no: int | None = None,
    ) -> LLMResponse:
        self._check_budget(role)

        role_conf = self._config["roles"][role]
        provider, model, max_tokens = role_conf["provider"], role_conf["model"], role_conf["max_tokens"]
        client = self._client_for(provider)

        prompt_sha = hashlib.sha256(json.dumps(messages, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

        started = time.monotonic()
        resp = client.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens)
        latency_ms = int((time.monotonic() - started) * 1000)

        usage = resp.usage
        tokens_in, tokens_out = usage.prompt_tokens, usage.completion_tokens
        tokens_cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        cost = cost_usd(model, tokens_in, tokens_out, tokens_cached)

        with session_scope() as session:
            record_llm_call(
                session,
                run_id=run_id,
                step_no=step_no,
                role=role,
                provider=provider,
                model=model,
                tokens_in=tokens_in,
                tokens_cached=tokens_cached,
                tokens_out=tokens_out,
                cost_usd=cost,
                latency_ms=latency_ms,
                finish_reason=resp.choices[0].finish_reason,
                prompt_sha=prompt_sha,
                prompt_raw={"messages": messages},
            )
            upsert_daily_spend(session, model, tokens_in, tokens_out, cost)

        return LLMResponse(
            text=resp.choices[0].message.content,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            model=model,
            finish_reason=resp.choices[0].finish_reason,
        )
