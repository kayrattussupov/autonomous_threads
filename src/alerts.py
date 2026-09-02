import logging
import os

import requests

logger = logging.getLogger(__name__)


def send_telegram_alert(text: str) -> bool:
    """Best-effort Telegram alert. Never raises — a broken alert channel
    must not crash the agent that's trying to report a problem."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("Telegram alert skipped (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set): %s", text)
        return False

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except requests.RequestException:
        logger.exception("Telegram alert failed to send: %s", text)
        return False

    if resp.status_code != 200:
        logger.error("Telegram alert rejected (HTTP %s): %s", resp.status_code, text)
        return False
    return True
