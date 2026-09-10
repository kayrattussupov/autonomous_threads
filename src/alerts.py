import logging
import os

import requests

from src.db.engine import session_scope
from src.db.repo import save_telegram_alert

logger = logging.getLogger(__name__)


def send_telegram_alert(text: str, source: str = "unknown") -> bool:
    """Best-effort Telegram alert. Never raises — a broken alert channel
    must not crash the agent that's trying to report a problem. Every
    attempt (sent, rejected, or skipped) is also persisted to
    telegram_alerts for the dashboard, itself best-effort."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("telegram_alert source=%s status=skipped text=%r reason=%s", source, text[:200], "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set")
        _record(source, text, success=False, error_detail="not configured (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing)", chat_id=chat_id)
        return False

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.error("telegram_alert source=%s status=failed text=%r error=%s", source, text[:200], exc)
        _record(source, text, success=False, error_detail=str(exc), chat_id=chat_id)
        return False

    if resp.status_code != 200:
        error_detail = f"HTTP {resp.status_code}: {resp.text}"
        logger.error("telegram_alert source=%s status=failed text=%r error=%s", source, text[:200], error_detail)
        _record(source, text, success=False, error_detail=error_detail, chat_id=chat_id)
        return False

    try:
        message_id = resp.json().get("result", {}).get("message_id")
    except ValueError:
        message_id = None

    logger.info("telegram_alert source=%s status=ok text=%r", source, text[:200])
    _record(source, text, success=True, error_detail=None, chat_id=chat_id, message_id=message_id)
    return True


def _record(source: str, text: str, *, success: bool, error_detail: str | None, chat_id: str | None, message_id: int | None = None) -> None:
    try:
        with session_scope() as session:
            save_telegram_alert(
                session,
                source=source,
                text=text,
                success=success,
                error_detail=error_detail,
                chat_id=chat_id,
                message_id=message_id,
                retry_count=1,
            )
    except Exception:
        logger.exception("failed to persist telegram_alert row (source=%s)", source)
