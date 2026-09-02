from src.scheduler import build_scheduler


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


def trigger_hour(job) -> int:
    # APScheduler CronTrigger stores its fields as a list; find the "hour" field.
    for field in job.trigger.fields:
        if field.name == "hour":
            return int(str(field))
    raise AssertionError(f"no hour field on trigger {job.trigger}")
