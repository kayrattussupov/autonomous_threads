from unittest.mock import MagicMock

import httpx
import pytest
from openai import RateLimitError

from src.db.repo import insert_post
from src.scheduler import build_scheduler, run_content_agent_if_queue_low, run_analyst_agent_monthly


def _rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx.Response(429, request=request, json={"error": {"message": "rate limited"}})
    return RateLimitError("rate limited", response=response, body=None)


def test_build_scheduler_registers_two_daily_feed_miner_jobs():
    scheduler = build_scheduler()
    jobs = scheduler.get_jobs()

    feed_miner_jobs = [j for j in jobs if j.id.startswith("feed_miner")]
    assert len(feed_miner_jobs) == 2

    hours = sorted(trigger_hour(job) for job in feed_miner_jobs)
    assert hours == [8, 20]

    for job in feed_miner_jobs:
        assert job.func.__name__ == "run_feed_miner"


def test_build_scheduler_registers_content_and_publisher_jobs():
    scheduler = build_scheduler()
    jobs = {j.id: j for j in scheduler.get_jobs()}

    assert "content_agent_hourly" in jobs
    assert jobs["content_agent_hourly"].func.__name__ == "run_content_agent_if_queue_low"

    assert "publisher_every_10_min" in jobs
    assert jobs["publisher_every_10_min"].func.__name__ == "publish_scheduled_posts"


def test_run_content_agent_if_queue_low_runs_agent_when_scheduled_count_below_queue_depth(db_session, monkeypatch):
    monkeypatch.setattr("src.scheduler.load_settings", lambda: {"queue_depth": 5})
    for i in range(3):
        insert_post(db_session, text=f"scheduled {i}", category="educational", status="scheduled")
    db_session.commit()

    agent_instance = MagicMock()
    agent_class = MagicMock(return_value=agent_instance)
    monkeypatch.setattr("src.scheduler.ContentAgent", agent_class)

    run_content_agent_if_queue_low()

    agent_class.assert_called_once_with()
    agent_instance.run.assert_called_once_with(trigger="queue_low")


def test_run_content_agent_if_queue_low_skips_agent_when_scheduled_count_at_or_above_queue_depth(db_session, monkeypatch):
    monkeypatch.setattr("src.scheduler.load_settings", lambda: {"queue_depth": 3})
    for i in range(3):
        insert_post(db_session, text=f"scheduled {i}", category="educational", status="scheduled")
    db_session.commit()

    agent_instance = MagicMock()
    agent_class = MagicMock(return_value=agent_instance)
    monkeypatch.setattr("src.scheduler.ContentAgent", agent_class)

    run_content_agent_if_queue_low()

    agent_class.assert_not_called()
    agent_instance.run.assert_not_called()


def test_run_content_agent_if_queue_low_retries_on_rate_limit_then_succeeds(db_session, monkeypatch):
    monkeypatch.setattr("src.scheduler.load_settings", lambda: {"queue_depth": 5})
    monkeypatch.setattr("src.scheduler.time.sleep", lambda _seconds: None)
    for i in range(3):
        insert_post(db_session, text=f"scheduled {i}", category="educational", status="scheduled")
    db_session.commit()

    agent_instance = MagicMock()
    agent_instance.run.side_effect = [_rate_limit_error(), _rate_limit_error(), MagicMock()]
    agent_class = MagicMock(return_value=agent_instance)
    monkeypatch.setattr("src.scheduler.ContentAgent", agent_class)

    run_content_agent_if_queue_low()

    assert agent_instance.run.call_count == 3


def test_run_content_agent_if_queue_low_raises_after_exhausting_retries(db_session, monkeypatch):
    monkeypatch.setattr("src.scheduler.load_settings", lambda: {"queue_depth": 5})
    monkeypatch.setattr("src.scheduler.time.sleep", lambda _seconds: None)
    for i in range(3):
        insert_post(db_session, text=f"scheduled {i}", category="educational", status="scheduled")
    db_session.commit()

    agent_instance = MagicMock()
    agent_instance.run.side_effect = _rate_limit_error()
    agent_class = MagicMock(return_value=agent_instance)
    monkeypatch.setattr("src.scheduler.ContentAgent", agent_class)

    with pytest.raises(RateLimitError):
        run_content_agent_if_queue_low()

    assert agent_instance.run.call_count == 3


def test_build_scheduler_registers_reply_triage_job():
    scheduler = build_scheduler()
    jobs = {j.id: j for j in scheduler.get_jobs()}

    assert "reply_triage_every_3h" in jobs
    job = jobs["reply_triage_every_3h"]
    assert job.func.__name__ == "run_reply_triage"
    assert job.trigger.interval.total_seconds() == 3 * 3600


def trigger_hour(job) -> int:
    # APScheduler CronTrigger stores its fields as a list; find the "hour" field.
    for field in job.trigger.fields:
        if field.name == "hour":
            return int(str(field))
    raise AssertionError(f"no hour field on trigger {job.trigger}")


def test_build_scheduler_registers_analyst_nightly_recompute_job():
    scheduler = build_scheduler()
    jobs = {j.id: j for j in scheduler.get_jobs()}

    assert "analyst_nightly_recompute" in jobs
    job = jobs["analyst_nightly_recompute"]
    assert job.func.__name__ == "recompute_nightly_metrics"
    assert trigger_hour(job) == 3


def test_build_scheduler_registers_analyst_agent_monthly_job():
    scheduler = build_scheduler()
    jobs = {j.id: j for j in scheduler.get_jobs()}

    assert "analyst_agent_monthly" in jobs
    job = jobs["analyst_agent_monthly"]
    assert job.func.__name__ == "run_analyst_agent_monthly"
    assert trigger_hour(job) == 20
    day_field = next(f for f in job.trigger.fields if f.name == "day")
    assert str(day_field) == "1"


def test_run_analyst_agent_monthly_invokes_agent_with_cron_trigger(monkeypatch):
    agent_instance = MagicMock()
    agent_class = MagicMock(return_value=agent_instance)
    monkeypatch.setattr("src.scheduler.AnalystAgent", agent_class)

    run_analyst_agent_monthly()

    agent_class.assert_called_once_with()
    agent_instance.run.assert_called_once_with(trigger="cron")
