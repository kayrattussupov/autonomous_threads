"""One-time backfill of posts.sector for posts created before the topic
planner existed (docs/superpowers/specs/2026-09-16-topic-planner-design.md).
Idempotent: only touches posts with sector IS NULL. Seeds the sectors table
from settings first, so it can run right after the migration.

Run manually: `python -m scripts.backfill_post_sectors`
"""
from sqlalchemy import select

from src.config import load_settings
from src.content.topic_planner import normalize_sector, sync_seed_sectors
from src.db.engine import session_scope
from src.db.models import Post
from src.db.repo import get_active_sector_names
from src.llm.client import LLMClient

CLASSIFY_PROMPT = (
    "К какой сфере бизнеса относится этот пост в Threads? Выбери ровно одну "
    "сферу из списка и ответь только её названием, без пояснений. Если ни одна "
    "не подходит — ответь «нет».\n\nСферы:\n{sectors}\n\nПост:\n{text}"
)


def backfill_post_sectors(session, llm_client, sectors: list[str]) -> dict:
    allowed = set(sectors)
    sector_list = "\n".join(f"- {s}" for s in sectors)
    updated = 0
    skipped: list[tuple[int, str]] = []

    posts = session.execute(select(Post).where(Post.sector.is_(None)).order_by(Post.id)).scalars().all()
    for post in posts:
        response = llm_client.complete(
            role="classifier",
            messages=[{"role": "user", "content": CLASSIFY_PROMPT.format(sectors=sector_list, text=post.text)}],
        )
        answer = normalize_sector(response.text.strip().strip("«»\"'.- "))
        if answer in allowed:
            post.sector = answer
            updated += 1
        else:
            skipped.append((post.id, response.text[:80]))
    return {"updated": updated, "skipped": skipped}


def main() -> None:
    cfg = load_settings()["topic_planner"]
    llm_client = LLMClient()
    with session_scope() as session:
        sync_seed_sectors(session, cfg["seed_sectors"])
        session.flush()
        result = backfill_post_sectors(session, llm_client, get_active_sector_names(session))
    print(f"Updated {result['updated']} posts.")
    for post_id, answer in result["skipped"]:
        print(f"  skipped post {post_id}: classifier answered {answer!r}")


if __name__ == "__main__":
    main()
