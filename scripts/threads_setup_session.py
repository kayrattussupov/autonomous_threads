"""One-time manual Threads login. Run locally on a machine with a real
display: `python -m scripts.threads_setup_session`. Opens a visible Chrome
window, waits for you to log in by hand, then saves the resulting session
cookies to THREADS_COOKIES_PATH (data/threads_cookies.json by default) so
src.threads.browser.auth.login() can reuse the session headlessly afterwards
(e.g. inside the worker container, once that path is copied into the
threads_session volume).
"""
import sys

from src.threads.browser.auth import save_cookies
from src.threads.browser.driver import build_driver


def main() -> int:
    driver = build_driver(headless=False)
    try:
        driver.get("https://www.threads.com/login")

        print("=" * 50)
        print("Log into Threads in the browser window that just opened.")
        print("Come back here and press Enter once you're logged in.")
        print("=" * 50)
        input("Press Enter after logging in... ")

        save_cookies(driver)
        print("Session saved. You can now run the scheduler/feed_miner headlessly.")
        return 0
    finally:
        driver.quit()


if __name__ == "__main__":
    sys.exit(main())
