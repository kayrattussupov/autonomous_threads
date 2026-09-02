from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_bearer_token
from src.api.schemas import AgentRunOut, AgentStepOut
from src.db import repo

router = APIRouter(dependencies=[Depends(require_bearer_token)])


@router.get("/runs", response_model=list[AgentRunOut])
def get_runs(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[AgentRunOut]:
    return repo.list_agent_runs(db, limit=limit)


@router.get("/runs/{run_id}/steps", response_model=list[AgentStepOut])
def get_run_steps(run_id: int, db: Session = Depends(get_db)) -> list[AgentStepOut]:
    return repo.list_agent_steps(db, run_id)
