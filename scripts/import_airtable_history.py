"""T1.6: one-time import of historical posts from Airtable into `posts`,
backfilling metrics via the Threads Graph API for the last 90 days.

Run manually once: `python -m scripts.import_airtable_history`.
"""
import os
from datetime import datetime, timedelta, timezone

from pyairtable import Api

from src.db.engine import session_scope
from src.db.models import Post
from src.threads.write_client import ThreadsAPIError, ThreadsWriteClient

CATEGORY_FALLBACK = "educational"  # historical posts predate the category field; reclassify later if needed
SCORE_WEIGHTS = {"leads": 100, "conversations": 10, "replies": 1, "views": 0.01}


def compute_score(replies_count: int, views: int) -> float:
    # Historical Airtable rows have no leads/conversations tracking — only replies and views are known.
    return SCORE_WEIGHTS["replies"] * (replies_count or 0) + SCORE_WEIGHTS["views"] * (views or 0)


def main() -> None:
    api = Api(os.environ["AIRTABLE_API_KEY"])
    table = api.table(os.environ["AIRTABLE_BASE_ID"], os.environ["AIRTABLE_TABLE_NAME"])

    write_client = ThreadsWriteClient(os.environ["THREADS_ACCESS_TOKEN"], os.environ["THREADS_USER_ID"])
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)

    imported, skipped = 0, 0
    for record in table.all():
        fields = record["fields"]
        media_id = fields.get("ThreadsPostId")
        if not media_id:
            skipped += 1
            continue

        with session_scope() as session:
            existing = session.query(Post).filter_by(threads_media_id=media_id).one_or_none()
            if existing:
                skipped += 1
                continue

        views = likes = replies_count = quotes = 0
        published_at = fields.get("PublishedAt")
        posted_dt = datetime.fromisoformat(published_at) if published_at else None
        if posted_dt and posted_dt >= cutoff:
            try:
                insights = write_client.get_media_insights(media_id)
                views, likes, replies_count, quotes = (
                    insights["views"], insights["likes"], insights["replies"], insights["quotes"],
                )
            except ThreadsAPIError as exc:
                print(f"insights failed for {media_id}: {exc}")

        with session_scope() as session:
            session.add(Post(
                text=fields.get("PostText", ""),
                category=CATEGORY_FALLBACK,
                status="published",
                threads_media_id=media_id,
                posted_at=posted_dt,
                views=views,
                likes=likes,
                replies_count=replies_count,
                quotes=quotes,
                score=compute_score(replies_count, views),
                metrics_updated_at=datetime.now(timezone.utc) if posted_dt and posted_dt >= cutoff else None,
            ))
        imported += 1

    print(f"Imported {imported} posts, skipped {skipped} (already present or missing ThreadsPostId).")


if __name__ == "__main__":
    main()
