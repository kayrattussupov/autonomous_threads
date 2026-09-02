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


def trigger_hour(job) -> int:
    # APScheduler CronTrigger stores its fields as a list; find the "hour" field.
    for field in job.trigger.fields:
        if field.name == "hour":
            return int(str(field))
    raise AssertionError(f"no hour field on trigger {job.trigger}")
