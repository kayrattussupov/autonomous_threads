from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_bearer_token
from src.api.schemas import PostsPageOut
from src.db import repo

router = APIRouter(dependencies=[Depends(require_bearer_token)])


@router.get("/posts", response_model=PostsPageOut)
def get_posts(
    category: str | None = None,
    style_variant_id: int | None = None,
    model_used: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PostsPageOut:
    filters = dict(category=category, style_variant_id=style_variant_id, model_used=model_used, status=status)
    items, total = repo.list_posts(db, page=page, page_size=page_size, **filters)
    median = repo.median_post_score(db, **filters)
    return PostsPageOut(items=items, total=total, page=page, page_size=page_size, median_score=median)
