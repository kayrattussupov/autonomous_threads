import time

from apscheduler.schedulers.background import BackgroundScheduler
from openai import RateLimitError

from src.agents.analyst import AnalystAgent, recompute_nightly_metrics
from src.agents.content import ContentAgent
from src.agents.feed_miner import run_feed_miner
from src.agents.publisher import publish_scheduled_posts
from src.agents.reply_triage import run_reply_triage
from src.config import load_settings
from src.db.engine import session_scope
from src.db.repo import count_scheduled_posts

TIMEZONE = "Asia/Almaty"

CONTENT_AGENT_MAX_ATTEMPTS = 3
CONTENT_AGENT_RETRY_BACKOFF_SECONDS = 5


def _run_with_rate_limit_retry(fn, *, max_attempts=CONTENT_AGENT_MAX_ATTEMPTS, backoff_seconds=CONTENT_AGENT_RETRY_BACKOFF_SECONDS):
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except RateLimitError:
            if attempt == max_attempts:
                raise
            time.sleep(backoff_seconds * attempt)


def run_content_agent_if_queue_low():
    queue_depth = load_settings()["queue_depth"]
    with session_scope() as session:
        scheduled_count = count_scheduled_posts(session)

    if scheduled_count >= queue_depth:
        return

    _run_with_rate_limit_retry(lambda: ContentAgent().run(trigger="queue_low"))


def run_analyst_agent_monthly():
    # AnalystAgent runs only once a month (see build_scheduler below) — losing
    # a run to a transient RateLimitError with no retry would mean no analyst
    # proposals for an entire month, so this gets the same retry treatment as
    # run_content_agent_if_queue_low above.
    _run_with_rate_limit_retry(lambda: AnalystAgent().run(trigger="cron"))


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        run_feed_miner, trigger="cron", hour=8, minute=0,
        id="feed_miner_morning", kwargs={"trigger": "cron"},
    )
    scheduler.add_job(
        run_feed_miner, trigger="cron", hour=20, minute=0,
        id="feed_miner_evening", kwargs={"trigger": "cron"},
    )
    scheduler.add_job(
        run_content_agent_if_queue_low, trigger="cron", minute=0,
        id="content_agent_hourly",
    )
    scheduler.add_job(
        publish_scheduled_posts, trigger="interval", minutes=10,
        id="publisher_every_10_min", kwargs={"trigger": "cron"},
    )
    scheduler.add_job(
        run_reply_triage, trigger="interval", hours=3,
        id="reply_triage_every_3h", kwargs={"trigger": "cron"},
    )
    scheduler.add_job(
        recompute_nightly_metrics, trigger="cron", hour=3, minute=0,
        id="analyst_nightly_recompute", kwargs={"trigger": "cron"},
    )
    scheduler.add_job(
        run_analyst_agent_monthly, trigger="cron", day=1, hour=20, minute=0,
        id="analyst_agent_monthly",
    )
    return scheduler


def main():
    scheduler = build_scheduler()
    scheduler.start()
    print(f"worker started -- feed_miner 08:00/20:00, content_agent hourly, publisher every 10min, reply_triage every 3h, analyst recompute nightly 03:00, analyst_agent monthly ({TIMEZONE})")
    try:
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    main()
