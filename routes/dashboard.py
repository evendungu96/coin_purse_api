# routes/dashboard.py
from __future__ import annotations

from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from common.db.config import get_db
from helpers.db_utils import active_query, require_owned_active
from models.models import (
    Account,
    Budget,
    BudgetItem,
    Category,
    RecurringTransaction,
    Transaction,
    TransactionKind,
)

router = APIRouter(prefix="/users/{user_id}/dashboard", tags=["dashboard"])


def month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


@router.get("/monthly")
def monthly_dashboard(
    user_id: UUID,
    year: Annotated[int, Query(ge=2000, le=2100)],
    month: Annotated[int, Query(ge=1, le=12)],
    db: Annotated[Session, Depends(get_db)],
):
    start, end = month_bounds(year, month)

    base = (
        active_query(db, Transaction)
        .filter(Transaction.user_id == user_id)
        .filter(Transaction.posted_at >= start, Transaction.posted_at <= end)
        .join(TransactionKind, Transaction.kind_id == TransactionKind.id)
        .filter(TransactionKind.is_active.is_(True))
    )

    totals = base.with_entities(
        func.coalesce(
            func.sum(
                case((TransactionKind.name == "income", Transaction.amount), else_=0)
            ),
            0,
        ).label("income"),
        func.coalesce(
            func.sum(
                case((TransactionKind.name == "expense", Transaction.amount), else_=0)
            ),
            0,
        ).label("expense"),
        func.coalesce(
            func.sum(
                case((TransactionKind.name == "refund", Transaction.amount), else_=0)
            ),
            0,
        ).label("refund"),
        func.coalesce(
            func.sum(
                case((TransactionKind.name == "transfer", Transaction.amount), else_=0)
            ),
            0,
        ).label("transfer"),
    ).first()

    income = Decimal(str(totals.income))
    expense = Decimal(str(totals.expense))
    refund = Decimal(str(totals.refund))
    net = income - expense + refund

    spend_by_category = (
        base.filter(TransactionKind.name == "expense")
        .join(Category, Transaction.category_id == Category.id)
        .filter(Category.is_active.is_(True))
        .with_entities(
            Category.id.label("category_id"),
            Category.name.label("category_name"),
            func.coalesce(func.sum(Transaction.amount), 0).label("spent"),
        )
        .group_by(Category.id, Category.name)
        .order_by(func.sum(Transaction.amount).desc())
        .all()
    )

    # Account movement (outgoing/incoming combined)
    out_rows = (
        base.join(Account, Transaction.account_id == Account.id)
        .filter(Account.is_active.is_(True))
        .with_entities(
            Account.id.label("account_id"),
            Account.name.label("account_name"),
            func.coalesce(
                func.sum(
                    case(
                        (TransactionKind.name == "income", Transaction.amount),
                        (TransactionKind.name == "refund", Transaction.amount),
                        (TransactionKind.name == "expense", -Transaction.amount),
                        (TransactionKind.name == "transfer", -Transaction.amount),
                        else_=0,
                    )
                ),
                0,
            ).label("net_change"),
        )
        .group_by(Account.id, Account.name)
        .all()
    )

    in_transfer_rows = (
        base.filter(
            TransactionKind.name == "transfer", Transaction.to_account_id.isnot(None)
        )
        .join(Account, Transaction.to_account_id == Account.id)
        .filter(Account.is_active.is_(True))
        .with_entities(
            Account.id.label("account_id"),
            Account.name.label("account_name"),
            func.coalesce(func.sum(Transaction.amount), 0).label("incoming_transfer"),
        )
        .group_by(Account.id, Account.name)
        .all()
    )

    acct_map: dict[str, dict] = {}
    for r in out_rows:
        acct_map[str(r.account_id)] = {
            "account_id": r.account_id,
            "account_name": r.account_name,
            "net_change": Decimal(str(r.net_change)),
        }
    for r in in_transfer_rows:
        key = str(r.account_id)
        if key not in acct_map:
            acct_map[key] = {
                "account_id": r.account_id,
                "account_name": r.account_name,
                "net_change": Decimal(0),
            }
        acct_map[key]["net_change"] += Decimal(str(r.incoming_transfer))

    account_movements = sorted(
        acct_map.values(), key=lambda x: x["net_change"], reverse=True
    )

    return {
        "period": {"year": year, "month": month, "start": start, "end": end},
        "totals": {
            "income": income,
            "expense": expense,
            "refund": refund,
            "net": net,
            "transfer_volume": Decimal(str(totals.transfer)),
        },
        "spend_by_category": [
            {
                "category_id": r.category_id,
                "category_name": r.category_name,
                "spent": Decimal(str(r.spent)),
            }
            for r in spend_by_category
        ],
        "account_movements": account_movements,
    }


@router.get("/{budget_id}/dashboard")
def budget_dashboard(
    user_id: UUID,
    budget_id: UUID,
    year: Annotated[int, Query(ge=2000, le=2100)],
    month: Annotated[int, Query(ge=1, le=12)],
    db: Annotated[Session, Depends(get_db)],
):
    # Ensure budget exists, active, and owned
    budget = require_owned_active(
        db, Budget, budget_id, user_id, detail="Budget not found"
    )

    month_start, month_end = month_bounds(year, month)

    # We only count spend for dates within BOTH:
    # - the requested month range
    # - the budget period range
    start = max(month_start, budget.period_start)
    end = min(month_end, budget.period_end)

    # If month is outside budget period, return empty but explicit.
    if end < start:
        return {
            "budget": {
                "budget_id": budget.id,
                "name": budget.name,
                "period_start": budget.period_start,
                "period_end": budget.period_end,
                "effective_start": start,
                "effective_end": end,
            },
            "summary": {
                "total_limit": Decimal("0.00"),
                "total_spent": Decimal("0.00"),
                "total_remaining": Decimal("0.00"),
                "percent_used": Decimal("0.00"),
            },
            "items": [],
        }

    # Get expense kind_id (lookup table)
    expense_kind = (
        active_query(db, TransactionKind)
        .filter(TransactionKind.name == "expense")
        .first()
    )
    if not expense_kind:
        # should never happen if seeded
        raise HTTPException(
            status_code=500, detail="Missing seeded transaction kind: expense"
        )

    # Active budget items + their categories
    items = (
        active_query(db, BudgetItem)
        .filter(BudgetItem.budget_id == budget_id)
        .join(Category, BudgetItem.category_id == Category.id)
        .filter(Category.is_active.is_(True))
        .with_entities(
            BudgetItem.id.label("item_id"),
            BudgetItem.category_id.label("category_id"),
            Category.name.label("category_name"),
            BudgetItem.limit_amount.label("limit_amount"),
        )
        .all()
    )

    if not items:
        return {
            "budget": {
                "budget_id": budget.id,
                "name": budget.name,
                "period_start": budget.period_start,
                "period_end": budget.period_end,
                "effective_start": start,
                "effective_end": end,
            },
            "summary": {
                "total_limit": Decimal("0.00"),
                "total_spent": Decimal("0.00"),
                "total_remaining": Decimal("0.00"),
                "percent_used": Decimal("0.00"),
            },
            "items": [],
        }

    category_ids = [r.category_id for r in items]

    # Spend per category = sum(expense transactions.amount) in effective range
    spend_rows = (
        active_query(db, Transaction)
        .filter(Transaction.user_id == user_id)
        .filter(Transaction.kind_id == expense_kind.id)
        .filter(Transaction.posted_at >= start, Transaction.posted_at <= end)
        .filter(Transaction.category_id.in_(category_ids))
        .with_entities(
            Transaction.category_id.label("category_id"),
            func.coalesce(func.sum(Transaction.amount), 0).label("spent"),
        )
        .group_by(Transaction.category_id)
        .all()
    )

    spend_map = {r.category_id: Decimal(str(r.spent)) for r in spend_rows}

    # Build item results
    out_items = []
    total_limit = Decimal("0.00")
    total_spent = Decimal("0.00")

    for r in items:
        limit_amt = Decimal(str(r.limit_amount))
        spent_amt = spend_map.get(r.category_id, Decimal("0.00"))
        remaining = limit_amt - spent_amt
        percent_used = (
            (spent_amt / limit_amt * Decimal(100)) if limit_amt > 0 else Decimal("0.00")
        )

        out_items.append(
            {
                "budget_item_id": r.item_id,
                "category_id": r.category_id,
                "category_name": r.category_name,
                "limit": limit_amt,
                "spent": spent_amt,
                "remaining": remaining,
                "percent_used": percent_used,
            }
        )

        total_limit += limit_amt
        total_spent += spent_amt

    total_remaining = total_limit - total_spent
    total_percent_used = (
        (total_spent / total_limit * Decimal(100))
        if total_limit > 0
        else Decimal("0.00")
    )

    # Order by most overspent / most used
    out_items.sort(key=lambda x: (x["percent_used"], x["spent"]), reverse=True)

    return {
        "budget": {
            "budget_id": budget.id,
            "name": budget.name,
            "period_start": budget.period_start,
            "period_end": budget.period_end,
            "effective_start": start,
            "effective_end": end,
        },
        "summary": {
            "total_limit": total_limit,
            "total_spent": total_spent,
            "total_remaining": total_remaining,
            "percent_used": total_percent_used,
        },
        "items": out_items,
    }


# ---------------------------------------------------------------------------
# Unified dashboard — single call for the frontend
# ---------------------------------------------------------------------------


def _account_balances(db: Session, user_id: UUID) -> list[dict]:
    """Return all active accounts with current_balance computed from transactions."""
    accounts = active_query(db, Account).filter(Account.user_id == user_id).all()

    # Income/refund/incoming-transfer are credits; expense/outgoing-transfer are debits.
    income_kind = (
        active_query(db, TransactionKind)
        .filter(TransactionKind.name == "income")
        .first()
    )
    expense_kind = (
        active_query(db, TransactionKind)
        .filter(TransactionKind.name == "expense")
        .first()
    )
    refund_kind = (
        active_query(db, TransactionKind)
        .filter(TransactionKind.name == "refund")
        .first()
    )
    transfer_kind = (
        active_query(db, TransactionKind)
        .filter(TransactionKind.name == "transfer")
        .first()
    )

    kind_ids = {
        k.name: k.id
        for k in [income_kind, expense_kind, refund_kind, transfer_kind]
        if k
    }

    # Credits per account (income + refund on account_id, incoming transfers on to_account_id)
    credit_rows = (
        active_query(db, Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.kind_id.in_([kind_ids.get("income"), kind_ids.get("refund")]),
        )
        .with_entities(
            Transaction.account_id,
            func.coalesce(func.sum(Transaction.amount), 0).label("total"),
        )
        .group_by(Transaction.account_id)
        .all()
    )
    incoming_transfer_rows = (
        active_query(db, Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.kind_id == kind_ids.get("transfer"),
        )
        .filter(Transaction.to_account_id.isnot(None))
        .with_entities(
            Transaction.to_account_id,
            func.coalesce(func.sum(Transaction.amount), 0).label("total"),
        )
        .group_by(Transaction.to_account_id)
        .all()
    )
    debit_rows = (
        active_query(db, Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.kind_id.in_(
                [kind_ids.get("expense"), kind_ids.get("transfer")]
            ),
        )
        .with_entities(
            Transaction.account_id,
            func.coalesce(func.sum(Transaction.amount), 0).label("total"),
        )
        .group_by(Transaction.account_id)
        .all()
    )

    credit_map: dict[str, Decimal] = {}
    for r in credit_rows:
        credit_map[str(r.account_id)] = credit_map.get(
            str(r.account_id), Decimal(0)
        ) + Decimal(str(r.total))
    for r in incoming_transfer_rows:
        credit_map[str(r.to_account_id)] = credit_map.get(
            str(r.to_account_id), Decimal(0)
        ) + Decimal(str(r.total))

    debits: dict[str, Decimal] = {
        str(r.account_id): Decimal(str(r.total)) for r in debit_rows
    }

    result = []
    for acct in accounts:
        key = str(acct.id)
        balance = (
            Decimal(str(acct.opening_balance))
            + credit_map.get(key, Decimal(0))
            - debits.get(key, Decimal(0))
        )
        result.append(
            {
                "account_id": acct.id,
                "account_name": acct.name,
                "account_type": acct.account_type,
                "opening_balance": Decimal(str(acct.opening_balance)),
                "current_balance": balance,
            }
        )
    return sorted(result, key=lambda x: x["account_name"])


def _current_budget_summary(db: Session, user_id: UUID, today: date) -> dict | None:
    """Find the budget whose period contains today; return item-level spend progress."""
    budget = (
        active_query(db, Budget)
        .filter(
            Budget.user_id == user_id,
            Budget.period_start <= today,
            Budget.period_end >= today,
        )
        .order_by(Budget.period_start.desc())
        .first()
    )
    if not budget:
        return None

    expense_kind = (
        active_query(db, TransactionKind)
        .filter(TransactionKind.name == "expense")
        .first()
    )
    if not expense_kind:
        return None

    items = (
        active_query(db, BudgetItem)
        .filter(BudgetItem.budget_id == budget.id)
        .join(Category, BudgetItem.category_id == Category.id)
        .filter(Category.is_active.is_(True))
        .with_entities(
            BudgetItem.id.label("item_id"),
            BudgetItem.category_id,
            Category.name.label("category_name"),
            BudgetItem.limit_amount,
        )
        .all()
    )

    category_ids = [r.category_id for r in items]
    spend_rows = (
        active_query(db, Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.kind_id == expense_kind.id,
            Transaction.posted_at >= budget.period_start,
            Transaction.posted_at <= budget.period_end,
            Transaction.category_id.in_(category_ids),
        )
        .with_entities(
            Transaction.category_id,
            func.coalesce(func.sum(Transaction.amount), 0).label("spent"),
        )
        .group_by(Transaction.category_id)
        .all()
    )
    spend_map = {r.category_id: Decimal(str(r.spent)) for r in spend_rows}

    # Group budget items by category (multiple items per category → sum limits)
    grouped: dict = {}
    for r in items:
        cid = r.category_id
        if cid not in grouped:
            grouped[cid] = {
                "category_id": cid,
                "category_name": r.category_name,
                "limit": Decimal(0),
            }
        grouped[cid]["limit"] += Decimal(str(r.limit_amount))

    out_items = []
    total_limit = Decimal(0)
    total_spent = Decimal(0)
    for g in grouped.values():
        limit_amt = g["limit"]
        spent_amt = spend_map.get(g["category_id"], Decimal(0))
        remaining = limit_amt - spent_amt
        percent_used = (spent_amt / limit_amt * 100) if limit_amt > 0 else Decimal(0)
        out_items.append(
            {
                "category_id": g["category_id"],
                "category_name": g["category_name"],
                "limit": limit_amt,
                "spent": spent_amt,
                "remaining": remaining,
                "percent_used": round(percent_used, 1),
                "over_budget": spent_amt > limit_amt,
            }
        )
        total_limit += limit_amt
        total_spent += spent_amt

    out_items.sort(key=lambda x: x["percent_used"], reverse=True)
    total_remaining = total_limit - total_spent
    total_percent_used = (
        (total_spent / total_limit * 100) if total_limit > 0 else Decimal(0)
    )

    return {
        "budget_id": budget.id,
        "budget_name": budget.name,
        "period_start": budget.period_start,
        "period_end": budget.period_end,
        "days_remaining": (budget.period_end - today).days,
        "summary": {
            "total_limit": total_limit,
            "total_spent": total_spent,
            "total_remaining": total_remaining,
            "percent_used": round(total_percent_used, 1),
        },
        "items": out_items,
    }


def _monthly_totals(db: Session, user_id: UUID, today: date) -> dict:
    """Income / expense / net for the current calendar month."""
    start, end = month_bounds(today.year, today.month)

    base = (
        active_query(db, Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.posted_at >= start,
            Transaction.posted_at <= end,
        )
        .join(TransactionKind, Transaction.kind_id == TransactionKind.id)
        .filter(TransactionKind.is_active.is_(True))
    )
    row = base.with_entities(
        func.coalesce(
            func.sum(
                case((TransactionKind.name == "income", Transaction.amount), else_=0)
            ),
            0,
        ).label("income"),
        func.coalesce(
            func.sum(
                case((TransactionKind.name == "expense", Transaction.amount), else_=0)
            ),
            0,
        ).label("expense"),
        func.coalesce(
            func.sum(
                case((TransactionKind.name == "refund", Transaction.amount), else_=0)
            ),
            0,
        ).label("refund"),
    ).first()

    income = Decimal(str(row.income))
    expense = Decimal(str(row.expense))
    refund = Decimal(str(row.refund))
    return {
        "year": today.year,
        "month": today.month,
        "income": income,
        "expense": expense,
        "refund": refund,
        "net": income - expense + refund,
    }


@router.get("")
def get_dashboard(user_id: UUID, db: Annotated[Session, Depends(get_db)]):
    """Single aggregated endpoint for the frontend dashboard.

    Returns:
    - account balances (opening + all-time net transactions)
    - current month income / expense / net
    - active budget progress (budget whose period contains today)
    - last 10 transactions
    - upcoming recurring transactions (next 30 days)
    """
    today = datetime.now(UTC).date()
    upcoming_cutoff = today + timedelta(days=30)

    accounts = _account_balances(db, user_id)
    total_net_worth = sum(a["current_balance"] for a in accounts)

    monthly = _monthly_totals(db, user_id, today)
    budget = _current_budget_summary(db, user_id, today)

    recent_txns = (
        active_query(db, Transaction)
        .filter(Transaction.user_id == user_id)
        .join(TransactionKind, Transaction.kind_id == TransactionKind.id)
        .join(Account, Transaction.account_id == Account.id)
        .filter(Account.is_active.is_(True))
        .with_entities(
            Transaction.id,
            Transaction.posted_at,
            Transaction.amount,
            Transaction.description,
            Transaction.account_id,
            Account.name.label("account_name"),
            Transaction.category_id,
            Transaction.to_account_id,
            TransactionKind.name.label("kind"),
        )
        .order_by(Transaction.posted_at.desc(), Transaction.created_at.desc())
        .limit(10)
        .all()
    )

    # Resolve category names for recent transactions
    cat_ids = {r.category_id for r in recent_txns if r.category_id}
    cat_names: dict = {}
    if cat_ids:
        cat_rows = (
            active_query(db, Category)
            .filter(Category.id.in_(cat_ids))
            .with_entities(Category.id, Category.name)
            .all()
        )
        cat_names = {r.id: r.name for r in cat_rows}

    upcoming_recurring = (
        active_query(db, RecurringTransaction)
        .filter(
            RecurringTransaction.user_id == user_id,
            RecurringTransaction.next_run_date >= today,
            RecurringTransaction.next_run_date <= upcoming_cutoff,
        )
        .join(TransactionKind, RecurringTransaction.kind_id == TransactionKind.id)
        .join(Account, RecurringTransaction.account_id == Account.id)
        .with_entities(
            RecurringTransaction.id,
            RecurringTransaction.description,
            RecurringTransaction.amount,
            RecurringTransaction.cadence,
            RecurringTransaction.next_run_date,
            RecurringTransaction.account_id,
            Account.name.label("account_name"),
            TransactionKind.name.label("kind"),
        )
        .order_by(RecurringTransaction.next_run_date)
        .all()
    )

    return {
        "as_of": today,
        "net_worth": total_net_worth,
        "accounts": accounts,
        "current_month": monthly,
        "active_budget": budget,
        "recent_transactions": [
            {
                "id": r.id,
                "posted_at": r.posted_at,
                "kind": r.kind,
                "amount": r.amount,
                "description": r.description,
                "account_id": r.account_id,
                "account_name": r.account_name,
                "category_id": r.category_id,
                "category_name": cat_names.get(r.category_id),
                "to_account_id": r.to_account_id,
            }
            for r in recent_txns
        ],
        "upcoming_recurring": [
            {
                "id": r.id,
                "description": r.description,
                "kind": r.kind,
                "amount": r.amount,
                "cadence": r.cadence,
                "next_run_date": r.next_run_date,
                "account_id": r.account_id,
                "account_name": r.account_name,
            }
            for r in upcoming_recurring
        ],
    }
