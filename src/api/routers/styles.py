from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_bearer_token
from src.api.schemas import StyleVariantOut
from src.db import repo

router = APIRouter(dependencies=[Depends(require_bearer_token)])


@router.get("/styles", response_model=list[StyleVariantOut])
def get_styles(db: Session = Depends(get_db)) -> list[StyleVariantOut]:
    return repo.list_style_variants(db)


@router.post("/styles/{variant_id}/approve", response_model=StyleVariantOut)
def approve_style(variant_id: int, db: Session = Depends(get_db)) -> StyleVariantOut:
    return repo.approve_style_variant(db, variant_id)


@router.post("/styles/{variant_id}/reject", response_model=StyleVariantOut)
def reject_style(variant_id: int, db: Session = Depends(get_db)) -> StyleVariantOut:
    return repo.reject_style_variant(db, variant_id)
