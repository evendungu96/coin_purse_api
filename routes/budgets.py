# routes/budgets.py
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from common.db.config import get_db
from helpers.db_utils import (
    active_query,
    get_or_reactivate,
    require_owned_active,
    soft_delete,
)
from models.models import Budget, BudgetItem
from schemas.budgets import BudgetClone, BudgetCreate, BudgetRead, BudgetUpdate

router = APIRouter(prefix="/users/{user_id}/budgets", tags=["budgets"])


@router.post("", response_model=BudgetRead, status_code=status.HTTP_201_CREATED)
def create_budget(
    user_id: UUID, payload: BudgetCreate, db: Annotated[Session, Depends(get_db)]
):
    return get_or_reactivate(
        db,
        Budget,
        [
            Budget.user_id == user_id,
            Budget.period_start == payload.period_start,
            Budget.period_end == payload.period_end,
        ],
        create=lambda: Budget(
            user_id=user_id,
            name=payload.name,
            period_start=payload.period_start,
            period_end=payload.period_end,
        ),
        updates={"name": payload.name, "is_template": False},
        conflict_detail="A budget for that period already exists.",
    )


@router.get("", response_model=list[BudgetRead])
def list_budgets(user_id: UUID, db: Annotated[Session, Depends(get_db)]):
    return (
        active_query(db, Budget)
        .filter(Budget.user_id == user_id)
        .order_by(Budget.period_start.desc())
        .all()
    )


@router.get("/{budget_id}", response_model=BudgetRead)
def get_budget(user_id: UUID, budget_id: UUID, db: Annotated[Session, Depends(get_db)]):
    return require_owned_active(
        db, Budget, budget_id, user_id, detail="Budget not found"
    )


@router.patch("/{budget_id}", response_model=BudgetRead)
def update_budget(
    user_id: UUID,
    budget_id: UUID,
    payload: BudgetUpdate,
    db: Annotated[Session, Depends(get_db)],
):
    budget = require_owned_active(
        db, Budget, budget_id, user_id, detail="Budget not found"
    )
    updates = payload.model_dump(exclude_unset=True)
    # Enforce single template per user — clear others before setting this one
    if updates.get("is_template") is True:
        db.query(Budget).filter(
            Budget.user_id == user_id,
            Budget.id != budget_id,
            Budget.is_template.is_(True),
        ).update({"is_template": False})
    for k, v in updates.items():
        setattr(budget, k, v)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=400, detail="Could not update budget (maybe duplicate period)."
        ) from exc
    db.refresh(budget)
    return budget


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_budget(
    user_id: UUID, budget_id: UUID, db: Annotated[Session, Depends(get_db)]
):
    _ = require_owned_active(db, Budget, budget_id, user_id, detail="Budget not found")
    deleted = soft_delete(db, Budget, budget_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Budget not found")


def _upsert_clone_items(db: Session, budget_id: UUID, source_items: list) -> None:
    """Copy source_items into budget_id.

    Reactivates + updates limit if a (budget_id, category_id) row already exists
    (even if soft-deleted), otherwise inserts fresh.  This avoids hitting the
    unique constraint when re-cloning into a previously soft-deleted budget.
    """
    for item in source_items:
        existing = (
            db.query(BudgetItem)
            .filter(
                BudgetItem.budget_id == budget_id,
                BudgetItem.category_id == item.category_id,
            )
            .first()
        )
        if existing:
            existing.is_active = True
            existing.limit_amount = item.limit_amount
            existing.name = item.name
        else:
            db.add(
                BudgetItem(
                    budget_id=budget_id,
                    category_id=item.category_id,
                    limit_amount=item.limit_amount,
                    name=item.name,
                )
            )


@router.post(
    "/{budget_id}/clone", response_model=BudgetRead, status_code=status.HTTP_201_CREATED
)
def clone_budget(
    user_id: UUID,
    budget_id: UUID,
    payload: BudgetClone,
    db: Annotated[Session, Depends(get_db)],
):
    """Clone a budget into a new period, copying all active items.

    The new budget's name defaults to the source budget's name if not provided.
    Raises 409 if a budget for that exact period already exists.
    """
    source = require_owned_active(
        db, Budget, budget_id, user_id, detail="Budget not found"
    )

    existing = (
        db.query(Budget)
        .filter(
            Budget.user_id == user_id,
            Budget.period_start == payload.period_start,
            Budget.period_end == payload.period_end,
        )
        .first()
    )
    if existing and existing.is_active:
        raise HTTPException(
            status_code=409, detail="A budget for that period already exists."
        )

    source_items = (
        active_query(db, BudgetItem).filter(BudgetItem.budget_id == source.id).all()
    )

    if existing:  # soft-deleted — reactivate and re-clone into it
        existing.is_active = True
        existing.name = payload.name
        existing.source_budget_id = source.id
        existing.is_template = False
        _upsert_clone_items(db, existing.id, source_items)
        db.commit()
        db.refresh(existing)
        return existing

    new_budget = Budget(
        user_id=user_id,
        name=payload.name,
        period_start=payload.period_start,
        period_end=payload.period_end,
        source_budget_id=source.id,
    )
    db.add(new_budget)
    db.flush()  # get new_budget.id before inserting items
    _upsert_clone_items(db, new_budget.id, source_items)
    db.commit()
    db.refresh(new_budget)
    return new_budget
