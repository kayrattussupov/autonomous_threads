from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_bearer_token
from src.api.schemas import SpendOut
from src.config import load_settings
from src.db.repo import get_month_to_date_cost_usd

router = APIRouter(dependencies=[Depends(require_bearer_token)])


@router.get("/spend", response_model=SpendOut)
def get_spend(db: Session = Depends(get_db)) -> SpendOut:
    settings = load_settings()
    cap_usd = settings["budget"]["hard_stop_usd"]
    spent = get_month_to_date_cost_usd(db)
    return SpendOut(month_to_date_usd=spent, cap_usd=cap_usd)
