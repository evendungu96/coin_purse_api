# routes/budget_items.py
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from common.db.config import get_db
from helpers.db_utils import (
    active_query,
    require_owned_active,
    soft_delete,
)
from models.models import Budget, BudgetItem, Category
from schemas.budgets import BudgetItemCreate, BudgetItemRead, BudgetItemUpdate

router = APIRouter(
    prefix="/users/{user_id}/budgets/{budget_id}/items", tags=["budget-items"]
)
flat_router = APIRouter(prefix="/users/{user_id}/budget-items", tags=["budget-items"])


def _require_budget(db: Session, user_id: UUID, budget_id: UUID):
    return require_owned_active(
        db, Budget, budget_id, user_id, detail="Budget not found"
    )


def _require_category(db: Session, user_id: UUID, category_id: UUID):
    return require_owned_active(
        db, Category, category_id, user_id, detail="Category not found"
    )


@router.post("", response_model=BudgetItemRead, status_code=status.HTTP_201_CREATED)
def create_budget_item(
    user_id: UUID,
    budget_id: UUID,
    payload: BudgetItemCreate,
    db: Annotated[Session, Depends(get_db)],
):
    _require_budget(db, user_id, budget_id)
    _require_category(db, user_id, payload.category_id)

    item = BudgetItem(
        budget_id=budget_id,
        category_id=payload.category_id,
        limit_amount=payload.limit_amount,
        name=payload.name,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("", response_model=list[BudgetItemRead])
def list_budget_items(
    user_id: UUID, budget_id: UUID, db: Annotated[Session, Depends(get_db)]
):
    _require_budget(db, user_id, budget_id)
    return active_query(db, BudgetItem).filter(BudgetItem.budget_id == budget_id).all()


@router.get("/{item_id}", response_model=BudgetItemRead)
def get_budget_item(
    user_id: UUID,
    budget_id: UUID,
    item_id: UUID,
    db: Annotated[Session, Depends(get_db)],
):
    _require_budget(db, user_id, budget_id)
    item = (
        active_query(db, BudgetItem)
        .filter(BudgetItem.budget_id == budget_id, BudgetItem.id == item_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Budget item not found")
    return item


@router.patch("/{item_id}", response_model=BudgetItemRead)
def update_budget_item(
    user_id: UUID,
    budget_id: UUID,
    item_id: UUID,
    payload: BudgetItemUpdate,
    db: Annotated[Session, Depends(get_db)],
):
    _require_budget(db, user_id, budget_id)
    item = (
        active_query(db, BudgetItem)
        .filter(BudgetItem.budget_id == budget_id, BudgetItem.id == item_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Budget item not found")

    updates = payload.model_dump(exclude_unset=True)
    if "category_id" in updates:
        _require_category(db, user_id, updates["category_id"])
    for k, v in updates.items():
        setattr(item, k, v)

    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_budget_item(
    user_id: UUID,
    budget_id: UUID,
    item_id: UUID,
    db: Annotated[Session, Depends(get_db)],
):
    _require_budget(db, user_id, budget_id)
    item = (
        active_query(db, BudgetItem)
        .filter(BudgetItem.budget_id == budget_id, BudgetItem.id == item_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Budget item not found")

    deleted = soft_delete(db, BudgetItem, item_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Budget item not found")


# ---------------------------------------------------------------------------
# Flat routes — budget_id is an optional query filter, not a path requirement
# ---------------------------------------------------------------------------


def _require_owned_item(db: Session, user_id: UUID, item_id: UUID) -> BudgetItem:
    """Fetch a BudgetItem and verify it belongs to a budget owned by user_id."""
    item = (
        active_query(db, BudgetItem)
        .join(Budget, Budget.id == BudgetItem.budget_id)
        .filter(
            BudgetItem.id == item_id,
            Budget.user_id == user_id,
            Budget.is_active.is_(True),
        )
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Budget item not found")
    return item


@flat_router.get("", response_model=list[BudgetItemRead])
def list_all_budget_items(
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    budget_id: UUID | None = None,
):
    q = (
        active_query(db, BudgetItem)
        .join(Budget, Budget.id == BudgetItem.budget_id)
        .filter(Budget.user_id == user_id, Budget.is_active.is_(True))
    )
    if budget_id is not None:
        q = q.filter(BudgetItem.budget_id == budget_id)
    return q.all()


@flat_router.get("/{item_id}", response_model=BudgetItemRead)
def get_budget_item_flat(
    user_id: UUID, item_id: UUID, db: Annotated[Session, Depends(get_db)]
):
    return _require_owned_item(db, user_id, item_id)


@flat_router.patch("/{item_id}", response_model=BudgetItemRead)
def update_budget_item_flat(
    user_id: UUID,
    item_id: UUID,
    payload: BudgetItemUpdate,
    db: Annotated[Session, Depends(get_db)],
):
    item = _require_owned_item(db, user_id, item_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(item, k, v)
    db.commit()
    db.refresh(item)
    return item


@flat_router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_budget_item_flat(
    user_id: UUID, item_id: UUID, db: Annotated[Session, Depends(get_db)]
):
    _require_owned_item(db, user_id, item_id)
    soft_delete(db, BudgetItem, item_id)
