from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_bearer_token
from src.api.schemas import SectorOut
from src.content.topic_planner import describe_sectors

router = APIRouter(dependencies=[Depends(require_bearer_token)])


@router.get("/sectors", response_model=list[SectorOut])
def get_sectors(db: Session = Depends(get_db)) -> list[SectorOut]:
    return describe_sectors(db)
