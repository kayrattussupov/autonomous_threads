import time

import pytest

from src.agents.base import ReActAgent, StepLimitExceeded
from src.db.models import AgentRun, AgentStep


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
