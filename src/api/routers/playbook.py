from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_bearer_token
from src.api.schemas import PlaybookRuleOut
from src.db import repo

router = APIRouter(dependencies=[Depends(require_bearer_token)])


@router.get("/playbook", response_model=list[PlaybookRuleOut])
def get_playbook(db: Session = Depends(get_db)) -> list[PlaybookRuleOut]:
    return repo.list_playbook_rules(db)


@router.post("/playbook/{rule_id}/approve", response_model=PlaybookRuleOut)
def approve_playbook(rule_id: int, db: Session = Depends(get_db)) -> PlaybookRuleOut:
    return repo.approve_playbook_rule(db, rule_id)


@router.post("/playbook/{rule_id}/reject", response_model=PlaybookRuleOut)
def reject_playbook(rule_id: int, db: Session = Depends(get_db)) -> PlaybookRuleOut:
    return repo.reject_playbook_rule(db, rule_id)
