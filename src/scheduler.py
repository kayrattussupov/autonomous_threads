import time

from apscheduler.schedulers.background import BackgroundScheduler

from src.agents.feed_miner import run_feed_miner

TIMEZONE = "Asia/Almaty"


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        run_feed_miner,
        trigger="cron",
        hour=8,
        minute=0,
        id="feed_miner_morning",
        kwargs={"trigger": "cron"},
    )
    scheduler.add_job(
        run_feed_miner,
        trigger="cron",
        hour=20,
        minute=0,
        id="feed_miner_evening",
        kwargs={"trigger": "cron"},
    )
    return scheduler


def main():
    scheduler = build_scheduler()
    scheduler.start()
    print(f"worker started — feed_miner scheduled 08:00/20:00 {TIMEZONE}")
    try:
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    main()
