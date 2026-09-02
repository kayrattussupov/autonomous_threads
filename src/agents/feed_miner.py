import hashlib
import re

from src.alerts import send_telegram_alert
from src.config import load_settings
from src.db.engine import session_scope
from src.db.repo import (
    add_agent_step,
    finish_agent_run,
    insert_swipe_file_post,
    start_agent_run,
    swipe_file_post_exists,
)
from src.llm.client import BudgetExceeded, LLMClient
from src.threads.read_client import AuthError, DailyViewCapExceeded, ThreadsReadClient

_POST_URL_ID_RE = re.compile(r"/post/([A-Za-z0-9_-]+)")

CLASSIFIER_PROMPT = (
    "Классифицируй тему поста в Threads одним-двумя словами на русском "
    "(например: автоматизация, маркетинг, найм, продажи, личный_бренд, другое). "
    "Ответь только темой, без пояснений.\n\nПост:\n{text}"
)


def _derive_post_id(url: str, text: str) -> str:
    """Stable id for dedup: prefer the id embedded in the post URL, fall
    back to a text hash when the URL doesn't contain one. Deliberately
    reimplemented here rather than importing threads_app's own
    make_post_id, to keep src/threads/read_client.py's lazy-import
    boundary the only place this codebase touches threads_app internals."""
    match = _POST_URL_ID_RE.search(url or "")
    if match:
        return match.group(1)
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _classify_topic(llm_client: LLMClient, text: str, run_id: int, step_no: int):
    return llm_client.complete(
        role="classifier",
        messages=[{"role": "user", "content": CLASSIFIER_PROMPT.format(text=text)}],
        run_id=run_id,
        step_no=step_no,
    )


def run_feed_miner(
    trigger: str = "cron",
    read_client: ThreadsReadClient | None = None,
    llm_client: LLMClient | None = None,
) -> dict:
    """Deterministic collector — NOT a ReActAgent (SPEC.md §12: pipelines
    here are rigid, agency would be cost without decision freedom). Still
    traced into agent_runs/agent_steps like the ReAct agents so the
    dashboard doesn't need a special case."""
    read_client = read_client or ThreadsReadClient()
    llm_client = llm_client or LLMClient()

    with session_scope() as session:
        run = start_agent_run(session, agent="feed_miner", trigger=trigger)
        run_id = run.id

    collected = 0
    skipped_dupes = 0
    step_no = 0
    status = "ok"
    error = None
    tokens_in = 0
    tokens_out = 0
    cost_usd = 0.0

    try:
        search_groups = load_settings()["search_groups"]

        for group in search_groups:
            if status == "failed":
                break
            for keyword in group["keywords"]:
                step_no += 1
                tool_ok = True
                tool_result = None
                try:
                    posts = read_client.search_keyword(keyword)
                    for post in posts:
                        text = post.get("text")
                        if not text:
                            continue  # malformed post — skip it, don't abort the run
                        url = post.get("url", "")
                        post_id = _derive_post_id(url, text)
                        with session_scope() as session:
                            already_seen = swipe_file_post_exists(session, post_id)
                        if already_seen:
                            skipped_dupes += 1
                            continue
                        classification = _classify_topic(llm_client, text, run_id, step_no)
                        tokens_in += classification.tokens_in
                        tokens_out += classification.tokens_out
                        cost_usd += classification.cost_usd
                        topic = classification.text.strip()
                        with session_scope() as session:
                            insert_swipe_file_post(
                                session,
                                threads_post_id=post_id,
                                text=text,
                                topic=topic,
                            )
                        collected += 1
                    tool_result = {"keyword": keyword, "posts_found": len(posts)}
                except (AuthError, DailyViewCapExceeded) as exc:
                    tool_ok = False
                    tool_result = str(exc)
                    status = "failed"
                    error = str(exc)
                    send_telegram_alert(f"feed_miner stopped: {exc}")

                with session_scope() as session:
                    add_agent_step(
                        session,
                        run_id=run_id,
                        step_no=step_no,
                        tool_name="search_keyword",
                        tool_args={"keyword": keyword},
                        tool_result=tool_result,
                        tool_ok=tool_ok,
                    )

                if status == "failed":
                    break
    except BudgetExceeded as exc:
        status = "budget_stop"
        error = str(exc)
        send_telegram_alert(f"feed_miner stopped (budget exceeded): {exc}")
    except Exception as exc:  # noqa: BLE001 — recorded, not swallowed silently
        status = "failed"
        error = str(exc)
        send_telegram_alert(f"feed_miner stopped (unexpected error): {exc}")

    with session_scope() as session:
        finish_agent_run(
            session,
            run_id,
            status=status,
            steps_count=step_no,
            error=error,
            output_ref=f"collected={collected} skipped_dupes={skipped_dupes}",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
        )

    return {"collected": collected, "skipped_dupes": skipped_dupes, "status": status}
