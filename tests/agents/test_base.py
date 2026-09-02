import time

import pytest

from src.agents.base import ReActAgent
from src.db.models import AgentRun, AgentStep
from src.llm.client import BudgetExceeded


class _EchoTool:
    def __init__(self):
        self.calls = 0

    def __call__(self, text: str) -> str:
        self.calls += 1
        return f"echo:{text}"


class _TwoStepAgent(ReActAgent):
    """Calls the echo tool twice then stops — used to prove the harness records exactly what happened."""

    def __init__(self, echo_tool, **kwargs):
        super().__init__(agent_name="test_agent", **kwargs)
        self._echo = echo_tool
        self._step = 0

    def tools(self) -> dict:
        return {"echo": self._echo}

    def system_prompt(self) -> str:
        return "test"

    def decide_next_action(self, history: list[dict]) -> dict | None:
        self._step += 1
        if self._step > 2:
            return None  # signal: done
        return {"thought": f"step {self._step}", "tool_name": "echo", "tool_args": {"text": str(self._step)}}


class _RunawayAgent(ReActAgent):
    """Never stops on its own — used to prove the step-limit hard-stops it."""

    def __init__(self, **kwargs):
        super().__init__(agent_name="runaway", **kwargs)

    def tools(self) -> dict:
        return {"noop": lambda: "ok"}

    def system_prompt(self) -> str:
        return "test"

    def decide_next_action(self, history: list[dict]) -> dict:
        return {"thought": "again", "tool_name": "noop", "tool_args": {}}


class _FailingToolAgent(ReActAgent):
    """Step 1 names a tool that doesn't exist; step 2 names one that raises; then stops.

    Proves both failure modes are recorded as a failed step (tool_ok=False) rather
    than aborting the whole run.
    """

    def __init__(self, **kwargs):
        super().__init__(agent_name="failing_tool_agent", **kwargs)
        self._step = 0

    def tools(self) -> dict:
        def _boom(**kwargs):
            raise RuntimeError("tool blew up")

        return {"boom": _boom}

    def system_prompt(self) -> str:
        return "test"

    def decide_next_action(self, history: list[dict]) -> dict | None:
        self._step += 1
        if self._step == 1:
            return {"thought": "call unknown tool", "tool_name": "does_not_exist", "tool_args": {}}
        if self._step == 2:
            return {"thought": "call failing tool", "tool_name": "boom", "tool_args": {}}
        return None  # signal: done


class _BudgetExceededToolAgent(ReActAgent):
    """Its one tool raises BudgetExceeded — proves this propagates to the
    outer handler (status="budget_stop") instead of being swallowed as an
    ordinary failed tool step by the inner try/except."""

    def __init__(self, **kwargs):
        super().__init__(agent_name="budget_exceeded_tool_agent", **kwargs)

    def tools(self) -> dict:
        def _blow_budget(**kwargs):
            raise BudgetExceeded("month-to-date spend exceeded hard stop")

        return {"spend": _blow_budget}

    def system_prompt(self) -> str:
        return "test"

    def decide_next_action(self, history: list[dict]) -> dict | None:
        return {"thought": "spend", "tool_name": "spend", "tool_args": {}}


def test_budget_exceeded_inside_tool_call_propagates_to_budget_stop(db_session):
    agent = _BudgetExceededToolAgent(max_steps=8, max_tokens=40_000, max_seconds=120)

    # ReActAgent.run()'s outer BudgetExceeded handler finishes the run with
    # status="budget_stop" and then re-raises (mirroring the LLM-call-site
    # BudgetExceeded path) — it does not return a snapshot in this case.
    with pytest.raises(BudgetExceeded):
        agent.run(trigger="manual")

    run_row = db_session.query(AgentRun).filter_by(agent="budget_exceeded_tool_agent").one()
    assert run_row.status == "budget_stop"
    # No step should have been recorded as an ordinary failed step for this —
    # the exception must propagate out of the per-step try/except entirely.
    steps = db_session.query(AgentStep).filter_by(run_id=run_row.id).all()
    assert steps == []


def test_unknown_and_failing_tool_calls_are_recorded_as_failed_steps(db_session):
    agent = _FailingToolAgent(max_steps=8, max_tokens=40_000, max_seconds=120)

    run = agent.run(trigger="manual")

    assert run.status == "ok"

    steps = db_session.query(AgentStep).filter_by(run_id=run.id).order_by(AgentStep.step_no).all()
    assert len(steps) == 2
    assert all(s.tool_ok is False for s in steps)
    assert steps[0].tool_name == "does_not_exist"
    assert steps[1].tool_name == "boom"


def test_agent_runs_recorded_steps(db_session):
    echo = _EchoTool()
    agent = _TwoStepAgent(echo, max_steps=8, max_tokens=40_000, max_seconds=120)

    run = agent.run(trigger="manual")

    assert run.status == "ok"
    assert echo.calls == 2

    steps = db_session.query(AgentStep).filter_by(run_id=run.id).order_by(AgentStep.step_no).all()
    assert [s.tool_name for s in steps] == ["echo", "echo"]
    assert steps[0].tool_result == "echo:1"


def test_step_limit_stops_at_nine(db_session):
    agent = _RunawayAgent(max_steps=8, max_tokens=40_000, max_seconds=120)

    run = agent.run(trigger="manual")

    assert run.status == "step_limit"
    assert run.steps_count == 8
    fetched = db_session.get(AgentRun, run.id)
    assert fetched.status == "step_limit"


def test_run_exposes_run_id_to_subclass(db_session):
    seen_run_ids = []

    class _RunIdCapturingAgent(ReActAgent):
        def __init__(self, **kwargs):
            super().__init__(agent_name="test_run_id", **kwargs)
            self._step = 0

        def tools(self):
            return {"noop": lambda: "ok"}

        def system_prompt(self):
            return "test"

        def decide_next_action(self, history):
            seen_run_ids.append(self._run_id)
            self._step += 1
            if self._step > 1:
                return None
            return {"thought": "t", "tool_name": "noop", "tool_args": {}}

    agent = _RunIdCapturingAgent(max_steps=8, max_tokens=40_000, max_seconds=120)
    run = agent.run(trigger="manual")

    assert seen_run_ids == [run.id, run.id]
    assert all(rid is not None for rid in seen_run_ids)
