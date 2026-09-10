from datetime import datetime, timezone

from fastapi.testclient import TestClient

from src.api.main import app
from src.db.models import AgentRun, AgentStep

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-token"}


def test_get_runs_requires_bearer_token():
    response = client.get("/runs")
    assert response.status_code == 401


def test_get_runs_returns_newest_first(db_session):
    older = AgentRun(agent="content", trigger="cron", started_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status="ok")
    newer = AgentRun(agent="content", trigger="cron", started_at=datetime(2026, 2, 1, tzinfo=timezone.utc), status="ok")
    db_session.add_all([older, newer])
    db_session.commit()

    response = client.get("/runs", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["id"] == newer.id
    assert body[1]["id"] == older.id


def test_get_run_steps_returns_steps_in_order(db_session):
    run = AgentRun(agent="content", trigger="cron", started_at=datetime.now(timezone.utc), status="running")
    db_session.add(run)
    db_session.flush()
    db_session.add(AgentStep(run_id=run.id, step_no=2, thought="second", tool_name="save_draft"))
    db_session.add(AgentStep(run_id=run.id, step_no=1, thought="first", tool_name="get_playbook"))
    db_session.commit()

    response = client.get(f"/runs/{run.id}/steps", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert [s["step_no"] for s in body] == [1, 2]


def test_get_run_steps_serializes_list_shaped_tool_result(db_session):
    # AnalystAgent's fetch_insights/sql/get_swipe_stats tools return a bare
    # list[dict] (not wrapped in a dict) whenever the result has <=30 rows,
    # the common case — AgentStepOut.tool_result must accept that shape or
    # this endpoint 500s on every ordinary AnalystAgent run.
    run = AgentRun(agent="analyst", trigger="cron", started_at=datetime.now(timezone.utc), status="running")
    db_session.add(run)
    db_session.flush()
    db_session.add(AgentStep(
        run_id=run.id, step_no=1, thought="fetch", tool_name="fetch_insights",
        tool_result=[{"id": 1, "score": 4.2}, {"id": 2, "score": 1.0}],
    ))
    db_session.commit()

    response = client.get(f"/runs/{run.id}/steps", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body[0]["tool_result"] == [{"id": 1, "score": 4.2}, {"id": 2, "score": 1.0}]
