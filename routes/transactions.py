# routes/transactions.py
from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from common.db.config import get_db
from helpers.db_utils import active_query, require_owned_active, soft_delete
from helpers.kinds import resolve_kind_id_or_400
from models.models import (
    Account,
    Budget,
    BudgetItem,
    Category,
    Transaction,
    TransactionKind,
)
from schemas.transactions import (
    KindName,
    TransactionCreate,
    TransactionRead,
    TransactionUpdate,
)

router = APIRouter(prefix="/users/{user_id}/transactions", tags=["transactions"])


def _require_owned_active_account(db: Session, user_id: UUID, account_id: UUID):
    return require_owned_active(
        db, Account, account_id, user_id, detail="Account not found"
    )


def _require_owned_active_category(db: Session, user_id: UUID, category_id: UUID):
    return require_owned_active(
        db, Category, category_id, user_id, detail="Category not found"
    )


@router.post("", response_model=TransactionRead, status_code=status.HTTP_201_CREATED)
def create_transaction(
    user_id: UUID, payload: TransactionCreate, db: Annotated[Session, Depends(get_db)]
):
    kind_id = resolve_kind_id_or_400(db, payload.kind)

    _require_owned_active_account(db, user_id, payload.account_id)

    transfer_group_id = None
    if payload.kind == "transfer":
        if not payload.to_account_id:
            raise HTTPException(
                status_code=400, detail="to_account_id required for transfer"
            )
        _require_owned_active_account(db, user_id, payload.to_account_id)
        if payload.category_id is not None:
            raise HTTPException(
                status_code=400, detail="category_id must be null for transfers"
            )
        transfer_group_id = payload.transfer_group_id or uuid4()
    else:
        if payload.to_account_id is not None:
            raise HTTPException(
                status_code=400,
                detail="to_account_id must be null for non-transfer transactions",
            )
        if payload.category_id is not None:
            _require_owned_active_category(db, user_id, payload.category_id)

    if payload.kind == "refund":
        if not payload.refunded_transaction_id:
            raise HTTPException(
                status_code=400, detail="refunded_transaction_id required for refunds"
            )
        _ = require_owned_active(
            db,
            Transaction,
            payload.refunded_transaction_id,
            user_id,
            detail="Original transaction for refund not found",
        )

    if payload.budget_item_id is not None:
        bi = (
            active_query(db, BudgetItem)
            .filter(BudgetItem.id == payload.budget_item_id)
            .first()
        )
        if not bi:
            raise HTTPException(status_code=404, detail="Budget item not found")
        require_owned_active(
            db, Budget, bi.budget_id, user_id, detail="Budget item not found"
        )

    txn = Transaction(
        user_id=user_id,
        kind_id=kind_id,
        account_id=payload.account_id,
        to_account_id=payload.to_account_id,
        category_id=payload.category_id,
        amount=payload.amount,
        description=payload.description,
        posted_at=payload.posted_at,
        refunded_transaction_id=payload.refunded_transaction_id,
        transfer_group_id=transfer_group_id,
        budget_item_id=payload.budget_item_id,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)

    return TransactionRead.model_validate(txn, from_attributes=True)


@router.get("", response_model=list[TransactionRead])
def list_transactions(
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    account_id: Annotated[UUID | None, Query()] = None,
    category_id: Annotated[UUID | None, Query()] = None,
    kind: Annotated[KindName | None, Query()] = None,
):
    q = active_query(db, Transaction).filter(Transaction.user_id == user_id)
    if start:
        q = q.filter(Transaction.posted_at >= start)
    if end:
        q = q.filter(Transaction.posted_at <= end)
    if account_id:
        q = q.filter(Transaction.account_id == account_id)
    if category_id:
        q = q.filter(Transaction.category_id == category_id)
    if kind:
        q = q.join(TransactionKind, Transaction.kind_id == TransactionKind.id).filter(
            TransactionKind.name == kind
        )

    txns = q.order_by(Transaction.posted_at.desc()).all()
    return [TransactionRead.model_validate(t, from_attributes=True) for t in txns]


@router.get("/{transaction_id}", response_model=TransactionRead)
def get_transaction(
    user_id: UUID, transaction_id: UUID, db: Annotated[Session, Depends(get_db)]
):
    txn = require_owned_active(
        db, Transaction, transaction_id, user_id, detail="Transaction not found"
    )
    return TransactionRead.model_validate(txn, from_attributes=True)


@router.patch("/{transaction_id}", response_model=TransactionRead)
def update_transaction(
    user_id: UUID,
    transaction_id: UUID,
    payload: TransactionUpdate,
    db: Annotated[Session, Depends(get_db)],
):
    txn = require_owned_active(
        db, Transaction, transaction_id, user_id, detail="Transaction not found"
    )

    data = payload.model_dump(exclude_unset=True)

    # Basic safety: validate new refs are active + owned
    if "account_id" in data and data["account_id"] is not None:
        _require_owned_active_account(db, user_id, data["account_id"])
    if "to_account_id" in data and data["to_account_id"] is not None:
        _require_owned_active_account(db, user_id, data["to_account_id"])
    if "category_id" in data and data["category_id"] is not None:
        _require_owned_active_category(db, user_id, data["category_id"])
    if "budget_item_id" in data and data["budget_item_id"] is not None:
        bi = (
            active_query(db, BudgetItem)
            .filter(BudgetItem.id == data["budget_item_id"])
            .first()
        )
        if not bi:
            raise HTTPException(status_code=404, detail="Budget item not found")
        require_owned_active(
            db, Budget, bi.budget_id, user_id, detail="Budget item not found"
        )

    for k, v in data.items():
        setattr(txn, k, v)

    db.commit()
    db.refresh(txn)
    return TransactionRead.model_validate(txn, from_attributes=True)


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_transaction(
    user_id: UUID, transaction_id: UUID, db: Annotated[Session, Depends(get_db)]
):
    _ = require_owned_active(
        db, Transaction, transaction_id, user_id, detail="Transaction not found"
    )
    deleted = soft_delete(db, Transaction, transaction_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Transaction not found")
