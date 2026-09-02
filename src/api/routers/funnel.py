from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_bearer_token
from src.api.schemas import FunnelMonthOut
from src.db import repo

router = APIRouter(dependencies=[Depends(require_bearer_token)])


@router.get("/funnel", response_model=list[FunnelMonthOut])
def get_funnel(
    months: int = Query(default=6, ge=1, le=24),
    db: Session = Depends(get_db),
) -> list[FunnelMonthOut]:
    return repo.get_funnel(db, months=months)
