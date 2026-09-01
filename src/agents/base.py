import time
from abc import ABC, abstractmethod

from src.db.engine import session_scope
from src.db.repo import add_agent_step, finish_agent_run, start_agent_run


class StepLimitExceeded(Exception):
    pass


class ReActAgent(ABC):
    def __init__(self, agent_name: str, max_steps: int = 8, max_tokens: int = 40_000, max_seconds: int = 120):
        self.agent_name = agent_name
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.max_seconds = max_seconds

    @abstractmethod
    def tools(self) -> dict:
        """tool_name -> callable(**tool_args) -> Any"""

    @abstractmethod
    def system_prompt(self) -> str:
        ...

    @abstractmethod
    def decide_next_action(self, history: list[dict]) -> dict | None:
        """Return {"thought", "tool_name", "tool_args"} or None to stop."""

    def run(self, trigger: str):
        with session_scope() as session:
            run = start_agent_run(session, agent=self.agent_name, trigger=trigger)
            run_id = run.id

        history: list[dict] = []
        tokens_used = 0
        started = time.monotonic()
        status = "ok"
        step_no = 0

        try:
            for step_no in range(1, self.max_steps + 1):
                if time.monotonic() - started > self.max_seconds:
                    status = "step_limit"
                    break
                if tokens_used > self.max_tokens:
                    status = "step_limit"
                    break

                action = self.decide_next_action(history)
                if action is None:
                    break

                tool_name = action["tool_name"]
                tool_args = action.get("tool_args", {})
                tool = self.tools()[tool_name]

                tool_started = time.monotonic()
                try:
                    result = tool(**tool_args)
                    tool_ok = True
                except Exception as exc:  # noqa: BLE001 — recorded, not swallowed silently
                    result = str(exc)
                    tool_ok = False
                tool_ms = int((time.monotonic() - tool_started) * 1000)

                with session_scope() as session:
                    add_agent_step(
                        session,
                        run_id=run_id,
                        step_no=step_no,
                        thought=action.get("thought"),
                        tool_name=tool_name,
                        tool_args=tool_args,
                        tool_result=result if isinstance(result, (dict, list, str, int, float, bool, type(None))) else str(result),
                        tool_ok=tool_ok,
                        tool_ms=tool_ms,
                    )

                history.append({"thought": action.get("thought"), "tool_name": tool_name, "tool_args": tool_args, "result": result})
            else:
                status = "step_limit"
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            with session_scope() as session:
                finish_agent_run(session, run_id, status=status, steps_count=step_no, error=str(exc))
            raise

        with session_scope() as session:
            finish_agent_run(session, run_id, status=status, steps_count=step_no)
            # session.get() returns the same identity-mapped instance that
            # finish_agent_run just mutated, so this reflects the pending
            # (not-yet-flushed) status/steps_count update. Do NOT call
            # session.refresh() here: refresh() does not autoflush, so it
            # would re-SELECT the still-committed (stale) row and silently
            # discard the pending update before session_scope's commit().
            run = session.get(type(run), run_id)
            # detach a plain snapshot so callers can read it after the session closes
            from src.db.models import AgentRun as _AgentRun
            snapshot = _AgentRun(**{c.name: getattr(run, c.name) for c in _AgentRun.__table__.columns})
        return snapshot
