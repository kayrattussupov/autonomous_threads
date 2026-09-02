import os
from datetime import datetime, timezone

from src.alerts import send_telegram_alert
from src.db.engine import session_scope
from src.db.repo import add_agent_step, finish_agent_run, get_posts_due_for_publish, start_agent_run
from src.threads.write_client import PublishingLimitExceeded, ThreadsAPIError, ThreadsWriteClient

PLACEHOLDER_STYLE_VARIANT_NAME = "v1_placeholder"


def _placeholder_genome_allowed() -> bool:
    return os.environ.get("ALLOW_PLACEHOLDER_GENOME", "").strip().lower() in {"1", "true", "yes"}


def publish_scheduled_posts(trigger: str = "cron", write_client: ThreadsWriteClient | None = None) -> dict:
    """Deterministic, not a ReActAgent — mirrors feed_miner's shape (Block 2).
    Publishes every due post in one pass; a PublishingLimitExceeded stops
    the whole batch (retrying more posts would only fail identically)."""
    write_client = write_client or ThreadsWriteClient(os.environ["THREADS_ACCESS_TOKEN"], os.environ["THREADS_USER_ID"])

    with session_scope() as session:
        run = start_agent_run(session, agent="content_publisher", trigger=trigger)
        run_id = run.id

    published, failed, blocked = 0, 0, 0
    status = "ok"
    error = None
    step_no = 0

    try:
        with session_scope() as session:
            due_post_ids = [p.id for p in get_posts_due_for_publish(session)]

        for post_id in due_post_ids:
            step_no += 1
            with session_scope() as session:
                from src.db.models import Post, StyleVariant
                post = session.get(Post, post_id)
                text = post.text
                style_variant = session.get(StyleVariant, post.style_variant_id) if post.style_variant_id is not None else None

            if style_variant is not None and style_variant.name == PLACEHOLDER_STYLE_VARIANT_NAME and not _placeholder_genome_allowed():
                blocked += 1
                tool_result = (
                    "blocked: active style_variant is the placeholder (v1_placeholder) — "
                    "replace its genome before real posting, or set ALLOW_PLACEHOLDER_GENOME=1 to override"
                )
                with session_scope() as session:
                    add_agent_step(session, run_id=run_id, step_no=step_no, tool_name="publish_text_post", tool_args={"post_id": post_id}, tool_result=tool_result, tool_ok=False)
                continue

            tool_ok = True
            tool_result = None
            try:
                media_id = write_client.publish_text_post(text)
                with session_scope() as session:
                    post = session.get(Post, post_id)
                    post.status = "published"
                    post.threads_media_id = media_id
                    post.posted_at = datetime.now(timezone.utc)
                published += 1
                tool_result = {"post_id": post_id, "media_id": media_id}
            except PublishingLimitExceeded as exc:
                tool_ok = False
                tool_result = str(exc)
                with session_scope() as session:
                    post = session.get(Post, post_id)
                    post.status = "failed"
                failed += 1

                # Remaining due posts were never attempted — leave them
                # "scheduled" (not "failed") so the next publisher_every_10_min
                # run retries them automatically. PublishingLimitExceeded is a
                # temporary daily-quota condition, not a permanent failure, and
                # Block 4's dashboard reads `status` as real signal — a post
                # that was never even attempted is not a "failed" post.
                send_telegram_alert(f"content_publisher: остановлен — {exc}")
                with session_scope() as session:
                    add_agent_step(session, run_id=run_id, step_no=step_no, tool_name="publish_text_post", tool_args={"post_id": post_id}, tool_result=tool_result, tool_ok=tool_ok)
                break
            except ThreadsAPIError as exc:
                tool_ok = False
                tool_result = str(exc)
                with session_scope() as session:
                    post = session.get(Post, post_id)
                    post.status = "failed"
                failed += 1
                send_telegram_alert(f"content_publisher: публикация не удалась (post_id={post_id}): {exc}")

            with session_scope() as session:
                add_agent_step(session, run_id=run_id, step_no=step_no, tool_name="publish_text_post", tool_args={"post_id": post_id}, tool_result=tool_result, tool_ok=tool_ok)
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        error = str(exc)
        send_telegram_alert(f"content_publisher: остановлен (неожиданная ошибка): {exc}")

    if blocked:
        send_telegram_alert(
            f"content_publisher: реальная публикация заблокирована — активный style_variant "
            f"это placeholder (v1_placeholder), заблокировано постов: {blocked}. "
            f"Замените genome перед реальной публикацией, или установите ALLOW_PLACEHOLDER_GENOME=1."
        )

    with session_scope() as session:
        finish_agent_run(session, run_id, status=status, steps_count=step_no, error=error, output_ref=f"published={published} failed={failed} blocked={blocked}")

    return {"published": published, "failed": failed, "blocked": blocked}
