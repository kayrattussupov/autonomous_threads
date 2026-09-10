from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_bearer_token
from src.api.schemas import TelegramAlertOut
from src.db import repo

router = APIRouter(dependencies=[Depends(require_bearer_token)])


@router.get("/telegram-alerts", response_model=list[TelegramAlertOut])
def get_telegram_alerts(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[TelegramAlertOut]:
    return repo.list_telegram_alerts(db, limit=limit)
