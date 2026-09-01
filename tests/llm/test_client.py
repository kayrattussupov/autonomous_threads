from datetime import date

import pytest

from src.db.models import DailySpend
from src.llm.client import BUDGET_HARD_STOP_USD, BudgetExceeded, LLMClient
from src.llm.pricing import cost_usd


def test_cost_usd_glm47():
    cost = cost_usd("glm-4.7", tokens_in=1000, tokens_out=500)
    assert round(cost, 8) == round((1000 * 0.60 + 500 * 2.20) / 1_000_000, 8)


def test_cost_usd_free_flash():
    assert cost_usd("glm-4.7-flash", tokens_in=100_000, tokens_out=50_000) == 0.0


def test_check_budget_raises_at_hard_stop(db_session, monkeypatch):
    db_session.add(DailySpend(date=date.today(), model="glm-4.7", tokens_in=0, tokens_out=0, cost_usd=BUDGET_HARD_STOP_USD))
    db_session.commit()

    client = LLMClient.__new__(LLMClient)  # skip __init__ — no config/keys needed for this check
    client._config = {"roles": {}}
    with pytest.raises(BudgetExceeded):
        client._check_budget(role="post_writer")


def test_check_budget_soft_stop_blocks_other_roles(db_session):
    db_session.add(DailySpend(date=date.today(), model="glm-4.7", tokens_in=0, tokens_out=0, cost_usd=8.5))
    db_session.commit()

    client = LLMClient.__new__(LLMClient)
    client._config = {"roles": {}}
    client._check_budget(role="post_writer")  # allowed
    with pytest.raises(BudgetExceeded):
        client._check_budget(role="analyst")
