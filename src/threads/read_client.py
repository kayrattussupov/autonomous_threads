import os
import random
import sys
import time

from src.db.engine import session_scope
from src.db.repo import get_daily_limit, increment_daily_limit


def _add_threads_app_to_path() -> None:
    threads_app_path = os.environ["THREADS_APP_PATH"]
    if threads_app_path not in sys.path:
        sys.path.insert(0, threads_app_path)


_add_threads_app_to_path()

from search.driver import build_driver  # noqa: E402  (must follow sys.path insert)
from search.auth import login  # noqa: E402
from search.scraper import scrape_keyword  # noqa: E402


class AuthError(Exception):
    pass


class DailyViewCapExceeded(Exception):
    pass


class ThreadsReadClient:
    def __init__(self, daily_view_cap: int = 200, min_delay_sec: float = 3.0, max_delay_sec: float = 15.0):
        self._cap = daily_view_cap
        self._min_delay = min_delay_sec
        self._max_delay = max_delay_sec

    def _check_and_increment_cap(self, n: int) -> None:
        with session_scope() as session:
            current = get_daily_limit(session, "feed_views")
            if current + n > self._cap:
                raise DailyViewCapExceeded(f"feed_views {current}+{n} would exceed daily cap {self._cap}")
            increment_daily_limit(session, "feed_views", by=n)

    def _jitter(self) -> None:
        time.sleep(random.uniform(self._min_delay, self._max_delay))

    def search_keyword(self, keyword: str, scroll_times: int = 5) -> list[dict]:
        self._check_and_increment_cap(scroll_times)

        driver = build_driver(headless=True)
        try:
            self._jitter()
            if not login(driver):
                raise AuthError(
                    f"Threads browser login failed for keyword search {keyword!r} — "
                    "stopping without retry, alert the operator"
                )
            self._jitter()
            return scrape_keyword(driver, keyword, scroll_times=scroll_times)
        finally:
            driver.quit()
