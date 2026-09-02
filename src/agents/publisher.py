import os
from datetime import datetime, timezone

from src.alerts import send_telegram_alert
from src.db.engine import session_scope
from src.db.repo import add_agent_step, finish_agent_run, get_posts_due_for_publish, start_agent_run
from src.threads.write_client import PublishingLimitExceeded, ThreadsAPIError, ThreadsWriteClient


def publish_scheduled_posts(trigger: str = "cron", write_client: ThreadsWriteClient | None = None) -> dict:
    """Deterministic, not a ReActAgent — mirrors feed_miner's shape (Block 2).
    Publishes every due post in one pass; a PublishingLimitExceeded stops
    the whole batch (retrying more posts would only fail identically)."""
    write_client = write_client or ThreadsWriteClient(os.environ["THREADS_ACCESS_TOKEN"], os.environ["THREADS_USER_ID"])

    with session_scope() as session:
        run = start_agent_run(session, agent="content_publisher", trigger=trigger)
        run_id = run.id

    published, failed = 0, 0
    status = "ok"
    error = None
    step_no = 0

    try:
        with session_scope() as session:
            due_post_ids = [p.id for p in get_posts_due_for_publish(session)]

        for idx, post_id in enumerate(due_post_ids):
            step_no += 1
            with session_scope() as session:
                from src.db.models import Post
                post = session.get(Post, post_id)
                text = post.text

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

                # Mark all remaining posts as failed to avoid leaving them "scheduled" forever
                remaining_post_ids = due_post_ids[idx + 1:]
                if remaining_post_ids:
                    with session_scope() as session:
                        from src.db.models import Post
                        for remaining_post_id in remaining_post_ids:
                            remaining_post = session.get(Post, remaining_post_id)
                            remaining_post.status = "failed"
                    failed += len(remaining_post_ids)

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

    with session_scope() as session:
        finish_agent_run(session, run_id, status=status, steps_count=step_no, error=error, output_ref=f"published={published} failed={failed}")

    return {"published": published, "failed": failed}
