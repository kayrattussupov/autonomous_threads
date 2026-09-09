import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.alerts import send_telegram_alert
from src.config import load_settings
from src.db.engine import session_scope
from src.db.models import Post
from src.db.repo import (
    add_agent_step,
    finish_agent_run,
    recompute_all_post_scores,
    recompute_playbook_evidence,
    recompute_style_variant_medians,
    start_agent_run,
)
from src.threads.write_client import ThreadsAPIError, ThreadsWriteClient


def recompute_nightly_metrics(trigger: str = "cron", write_client: ThreadsWriteClient | None = None) -> dict:
    """T6.1 — deterministic nightly recompute. NOT a ReActAgent (SPEC.md §12,
    same reasoning as feed_miner/reply_triage). Shares agent_runs.agent="analyst"
    with AnalystAgent (below), distinguished by trigger."""
    write_client = write_client or ThreadsWriteClient(os.environ["THREADS_ACCESS_TOKEN"], os.environ["THREADS_USER_ID"])

    with session_scope() as session:
        run = start_agent_run(session, agent="analyst", trigger=trigger)
        run_id = run.id

    step_no = 0
    status = "ok"
    error = None
    refreshed = 0
    refresh_failures = 0

    try:
        window_days = load_settings()["metrics_refresh_window_days"]
        since = datetime.now(timezone.utc) - timedelta(days=window_days)

        with session_scope() as session:
            posts = session.execute(
                select(Post.id, Post.threads_media_id).where(
                    Post.status == "published",
                    Post.threads_media_id.is_not(None),
                    Post.posted_at >= since,
                )
            ).all()

        for post_id, media_id in posts:
            step_no += 1
            tool_ok = True
            tool_result = None
            try:
                insights = write_client.get_media_insights(media_id)
                with session_scope() as session:
                    post = session.get(Post, post_id)
                    post.views = insights.get("views")
                    post.likes = insights.get("likes")
                    post.replies_count = insights.get("replies")
                    post.quotes = insights.get("quotes")
                    post.metrics_updated_at = datetime.now(timezone.utc)
                tool_result = {"post_id": post_id, "insights": insights}
                refreshed += 1
            except ThreadsAPIError as exc:
                # No systemic-vs-per-post distinction is available from
                # ThreadsAPIError alone (write_client's _request() already
                # exhausts its own 429 backoff before raising) — always skip
                # and continue, never abort the whole nightly run.
                tool_ok = False
                tool_result = str(exc)
                refresh_failures += 1

            with session_scope() as session:
                add_agent_step(
                    session, run_id=run_id, step_no=step_no,
                    tool_name="get_media_insights", tool_args={"post_id": post_id, "media_id": media_id},
                    tool_result=tool_result, tool_ok=tool_ok,
                )

        with session_scope() as session:
            scored = recompute_all_post_scores(session)
            recompute_style_variant_medians(session)
            promoted = recompute_playbook_evidence(session)
            step_no += 1
            add_agent_step(
                session, run_id=run_id, step_no=step_no,
                tool_name="recompute_scores_and_evidence", tool_args={},
                tool_result={"scored_posts": scored, "promoted_rule_ids": [r.id for r in promoted]},
                tool_ok=True,
            )
    except Exception as exc:  # noqa: BLE001 — recorded, not swallowed silently
        status = "failed"
        error = str(exc)
        send_telegram_alert(f"analyst nightly recompute stopped (unexpected error): {exc}")

    with session_scope() as session:
        finish_agent_run(
            session, run_id, status=status, steps_count=step_no, error=error,
            output_ref=f"refreshed={refreshed} refresh_failures={refresh_failures}",
        )

    return {"status": status, "refreshed": refreshed, "refresh_failures": refresh_failures}
