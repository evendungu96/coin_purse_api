# routes/views.py
from __future__ import annotations

import calendar as cal_lib
import datetime
from datetime import UTC
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from common.db.config import get_db
from helpers.db_utils import active_query
from models.models import (
    Account,
    Budget,
    BudgetItem,
    Category,
    Transaction,
    TransactionKind,
)
from routes.dashboard import get_dashboard

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


def _currency(value: object) -> str:
    return f"KES {float(value):,.2f}"  # type: ignore[arg-type]


def _date_fmt(value: object) -> str:
    return value.strftime("%b %d") if value else "—"  # type: ignore[union-attr]


def _month_name(value: object) -> str:
    return cal_lib.month_name[int(value)]  # type: ignore[arg-type]


def _kind_badge(kind: str) -> str:
    return {
        "income": "stamp stamp-green",
        "expense": "stamp stamp-red",
        "refund": "stamp stamp-blue",
        "transfer": "stamp stamp-blue",
    }.get(kind, "stamp stamp-blue")


templates.env.filters["currency"] = _currency
templates.env.filters["date_fmt"] = _date_fmt
templates.env.filters["month_name"] = _month_name
templates.env.filters["kind_badge"] = _kind_badge

router = APIRouter(tags=["ui"])


def _require_session(request: Request) -> str:
    """Redirect to /auth/login if the user is not in the session."""
    user_id = request.session.get("user_id")
    if not user_id:
        request.session["next"] = str(request.url)
        raise HTTPException(
            status_code=302,
            headers={"location": "/auth/login"},
        )
    return user_id


@router.get("/ui/dashboard/{user_id}", response_class=HTMLResponse)
def ui_dashboard(
    request: Request,
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _session_user: Annotated[str, Depends(_require_session)],
):
    data = get_dashboard(user_id, db)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            **data,
            "user_id": str(user_id),
            "session_user_name": request.session.get("user_name"),
        },
    )


@router.get("/ui/config/{user_id}", response_class=HTMLResponse)
def ui_config(
    request: Request,
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _session_user: Annotated[str, Depends(_require_session)],
):
    accounts = (
        active_query(db, Account)
        .filter(Account.user_id == user_id)
        .order_by(Account.name)
        .all()
    )
    categories = (
        active_query(db, Category)
        .filter(Category.user_id == user_id)
        .order_by(Category.name)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "config.html",
        {
            "user_id": str(user_id),
            "accounts": accounts,
            "categories": categories,
            "session_user_name": request.session.get("user_name"),
        },
    )


@router.get("/ui/budgets/{user_id}", response_class=HTMLResponse)
def ui_budgets(
    request: Request,
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _session_user: Annotated[str, Depends(_require_session)],
):
    today = datetime.datetime.now(UTC).date()
    budgets = (
        active_query(db, Budget)
        .filter(Budget.user_id == user_id)
        .order_by(Budget.period_start.desc())
        .all()
    )
    categories = (
        active_query(db, Category)
        .filter(Category.user_id == user_id)
        .order_by(Category.name)
        .all()
    )

    # Build source-budget name map (may include soft-deleted originals)
    active_id_map = {b.id: b.name for b in budgets}
    source_ids = {b.source_budget_id for b in budgets if b.source_budget_id}
    source_name_map = dict(active_id_map)
    missing = source_ids - active_id_map.keys()
    if missing:
        for b in db.query(Budget).filter(Budget.id.in_(missing)).all():
            source_name_map[b.id] = b.name

    budget_data = []
    for b in budgets:
        items = (
            active_query(db, BudgetItem)
            .filter(BudgetItem.budget_id == b.id)
            .join(Category, BudgetItem.category_id == Category.id)
            .filter(Category.is_active.is_(True))
            .with_entities(
                BudgetItem.id.label("id"),
                BudgetItem.limit_amount.label("limit_amount"),
                BudgetItem.category_id.label("category_id"),
                Category.name.label("category_name"),
            )
            .order_by(Category.name)
            .all()
        )
        # Next-month clone target based on period_end
        ny = b.period_end.year + (1 if b.period_end.month == 12 else 0)
        nm = 1 if b.period_end.month == 12 else b.period_end.month + 1
        clone_start = datetime.date(ny, nm, 1)
        clone_end = datetime.date(ny, nm, cal_lib.monthrange(ny, nm)[1])
        budget_data.append(
            {
                "budget": b,
                "items": items,
                "source_name": (
                    source_name_map.get(b.source_budget_id)
                    if b.source_budget_id
                    else None
                ),
                "clone_start": clone_start.isoformat(),
                "clone_end": clone_end.isoformat(),
                "clone_label": f"{cal_lib.month_name[nm]} {ny}",
                "is_current": b.period_start <= today <= b.period_end,
            }
        )

    # Trend data — non-template budgets in chronological order
    trend_entries = [bd for bd in reversed(budget_data) if not bd["budget"].is_template]
    trend_data = []
    cat_data: dict[str, list] = {}
    n = len(trend_entries)
    for i, bd in enumerate(trend_entries):
        b = bd["budget"]
        total = sum(float(item.limit_amount) for item in bd["items"])
        trend_data.append(
            {
                "name": b.name,
                "period_start": b.period_start.isoformat(),
                "total_limit": total,
                "item_count": len(bd["items"]),
            }
        )
        for item in bd["items"]:
            if item.category_name not in cat_data:
                cat_data[item.category_name] = [None] * n
            cat_data[item.category_name][i] = float(item.limit_amount)
    trend_labels = [t["name"] for t in trend_data]
    trend_series = [{"name": cat, "data": cat_data[cat]} for cat in sorted(cat_data)]
    trend_series.insert(
        0, {"name": "Total", "data": [t["total_limit"] for t in trend_data]}
    )
    trend_stats: dict = {}
    if trend_data:
        totals = [t["total_limit"] for t in trend_data]
        trend_stats = {
            "max": max(totals),
            "min": min(totals),
            "avg": sum(totals) / len(totals),
            "count": len(totals),
        }

    first_of_month = today.replace(day=1)
    last_of_month = today.replace(day=cal_lib.monthrange(today.year, today.month)[1])
    template_budget = next((b for b in budgets if b.is_template), None)
    return templates.TemplateResponse(
        request,
        "budgets.html",
        {
            "user_id": str(user_id),
            "budgets": budget_data,
            "categories": categories,
            "default_start": first_of_month.isoformat(),
            "default_end": last_of_month.isoformat(),
            "template_budget_id": str(template_budget.id) if template_budget else "",
            "trend_data": trend_data,
            "trend_stats": trend_stats,
            "trend_labels": trend_labels,
            "trend_series": trend_series,
            "session_user_name": request.session.get("user_name"),
        },
    )


@router.get("/ui/transactions/{user_id}", response_class=HTMLResponse)
def ui_transactions(
    request: Request,
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _session_user: Annotated[str, Depends(_require_session)],
):
    accounts = (
        active_query(db, Account)
        .filter(Account.user_id == user_id)
        .order_by(Account.name)
        .all()
    )
    categories = (
        active_query(db, Category)
        .filter(Category.user_id == user_id)
        .order_by(Category.name)
        .all()
    )
    kinds = active_query(db, TransactionKind).order_by(TransactionKind.name).all()

    # Recent transactions — last 50
    transactions = (
        active_query(db, Transaction)
        .filter(Transaction.user_id == user_id)
        .order_by(Transaction.posted_at.desc())
        .limit(50)
        .all()
    )

    # Stats: count by kind
    kind_counts_rows = (
        db.query(TransactionKind.name, func.count(Transaction.id).label("cnt"))
        .join(Transaction, Transaction.kind_id == TransactionKind.id)
        .filter(Transaction.user_id == user_id, Transaction.is_active.is_(True))
        .group_by(TransactionKind.name)
        .all()
    )
    kind_counts = {row.name: row.cnt for row in kind_counts_rows}

    # Stats: transactions per day for last 7 days
    today = datetime.datetime.now(UTC).date()
    last7 = [today - datetime.timedelta(days=i) for i in reversed(range(7))]
    daily_counts_rows = (
        db.query(Transaction.posted_at, func.count(Transaction.id).label("cnt"))
        .filter(
            Transaction.user_id == user_id,
            Transaction.is_active.is_(True),
            Transaction.posted_at >= last7[0],
        )
        .group_by(Transaction.posted_at)
        .all()
    )
    daily_map = {row.posted_at: row.cnt for row in daily_counts_rows}
    daily_labels = [d.strftime("%a %d") for d in last7]
    daily_data = [daily_map.get(d, 0) for d in last7]

    return templates.TemplateResponse(
        request,
        "transactions.html",
        {
            "user_id": str(user_id),
            "accounts": accounts,
            "categories": categories,
            "kinds": kinds,
            "transactions": transactions,
            "kind_counts": kind_counts,
            "daily_labels": daily_labels,
            "daily_data": daily_data,
            "session_user_name": request.session.get("user_name"),
        },
    )


@router.get("/ui/transactions/{user_id}/stats")
def ui_transactions_stats(
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _session_user: Annotated[str, Depends(_require_session)],
):
    kind_counts_rows = (
        db.query(TransactionKind.name, func.count(Transaction.id).label("cnt"))
        .join(Transaction, Transaction.kind_id == TransactionKind.id)
        .filter(Transaction.user_id == user_id, Transaction.is_active.is_(True))
        .group_by(TransactionKind.name)
        .all()
    )
    kind_counts = {row.name: row.cnt for row in kind_counts_rows}

    today = datetime.datetime.now(UTC).date()
    last7 = [today - datetime.timedelta(days=i) for i in reversed(range(7))]
    daily_counts_rows = (
        db.query(Transaction.posted_at, func.count(Transaction.id).label("cnt"))
        .filter(
            Transaction.user_id == user_id,
            Transaction.is_active.is_(True),
            Transaction.posted_at >= last7[0],
        )
        .group_by(Transaction.posted_at)
        .all()
    )
    daily_map = {row.posted_at: row.cnt for row in daily_counts_rows}
    return {
        "kind_counts": kind_counts,
        "daily_labels": [d.strftime("%a %d") for d in last7],
        "daily_data": [daily_map.get(d, 0) for d in last7],
    }
