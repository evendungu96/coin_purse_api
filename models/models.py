# SQLAlchemy 2.0 style models — sync-friendly, async-compatible

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(
        String(320), nullable=False, unique=True, index=True
    )
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True, unique=True)
    # Google OAuth — stable identifier from the `sub` claim of the ID token
    google_sub: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )

    accounts: Mapped[list[Account]] = relationship(back_populates="user")
    categories: Mapped[list[Category]] = relationship(back_populates="user")
    budgets: Mapped[list[Budget]] = relationship(back_populates="user")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="user")
    recurring: Mapped[list[RecurringTransaction]] = relationship(back_populates="user")
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class RefreshToken(TimestampMixin, Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_user_id", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Store a SHA-256 hash of the token, never the plaintext
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class Account(TimestampMixin, Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_accounts_user_name"),
        Index("ix_accounts_user_id", "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    account_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # e.g. cash/bank/card/mobile_money
    opening_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )

    user: Mapped[User] = relationship(back_populates="accounts")
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="account",
        foreign_keys="Transaction.account_id",
    )
    incoming_transfers: Mapped[list[Transaction]] = relationship(
        back_populates="to_account",
        foreign_keys="Transaction.to_account_id",
    )


class TransactionKind(TimestampMixin, Base):
    """
    Lookup-table 'enum' for transaction kinds:
      income, expense, transfer, refund
    """

    __tablename__ = "transaction_kinds"

    name: Mapped[str] = mapped_column(
        String(50), nullable=False, unique=True, index=True
    )

    transactions: Mapped[list[Transaction]] = relationship(back_populates="kind")
    recurring: Mapped[list[RecurringTransaction]] = relationship(back_populates="kind")


class Category(TimestampMixin, Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_categories_user_name"),
        Index("ix_categories_user_id", "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    user: Mapped[User] = relationship(back_populates="categories")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="category")
    budget_items: Mapped[list[BudgetItem]] = relationship(back_populates="category")


class Transaction(TimestampMixin, Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_transactions_amount_nonneg"),
        # For one-row transfers, prevent self-transfer
        CheckConstraint(
            "(to_account_id IS NULL) OR (account_id <> to_account_id)",
            name="ck_transactions_not_self_transfer",
        ),
        Index("ix_transactions_user_posted_at", "user_id", "posted_at"),
        Index(
            "ix_transactions_user_category_posted_at",
            "user_id",
            "category_id",
            "posted_at",
        ),
        Index(
            "ix_transactions_user_account_posted_at",
            "user_id",
            "account_id",
            "posted_at",
        ),
        Index("ix_transactions_kind_id", "kind_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transaction_kinds.id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # transfer destination (only for transfers)
    to_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=True,
    )

    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=True,
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    posted_at: Mapped[date] = mapped_column(Date, nullable=False)

    # optional: link refunds to original txn
    refunded_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # optional grouping for transfers (helps reporting/auditing)
    transfer_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="transactions")
    kind: Mapped[TransactionKind] = relationship(back_populates="transactions")

    account: Mapped[Account] = relationship(
        back_populates="transactions",
        foreign_keys=[account_id],
    )
    to_account: Mapped[Account | None] = relationship(
        back_populates="incoming_transfers",
        foreign_keys=[to_account_id],
    )

    category: Mapped[Category | None] = relationship(back_populates="transactions")

    refunded_transaction: Mapped[Transaction | None] = relationship(
        remote_side="Transaction.id",
        foreign_keys=[refunded_transaction_id],
    )


class Budget(TimestampMixin, Base):
    __tablename__ = "budgets"
    __table_args__ = (
        Index("ix_budgets_user_period", "user_id", "period_start", "period_end"),
        UniqueConstraint(
            "user_id", "period_start", "period_end", name="uq_budgets_user_period"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    is_template: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    source_budget_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("budgets.id", ondelete="SET NULL"),
        nullable=True,
    )

    user: Mapped[User] = relationship(back_populates="budgets")
    items: Mapped[list[BudgetItem]] = relationship(
        back_populates="budget", cascade="all, delete-orphan"
    )
    source_budget: Mapped[Budget | None] = relationship(
        foreign_keys=[source_budget_id], remote_side="Budget.id"
    )


class BudgetItem(TimestampMixin, Base):
    __tablename__ = "budget_items"
    __table_args__ = (
        CheckConstraint("limit_amount >= 0", name="ck_budget_items_limit_nonneg"),
        Index("ix_budget_items_budget_id", "budget_id"),
    )

    budget_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("budgets.id", ondelete="CASCADE"),
        nullable=False,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    limit_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    budget: Mapped[Budget] = relationship(back_populates="items")
    category: Mapped[Category] = relationship(back_populates="budget_items")


class RecurringTransaction(TimestampMixin, Base):
    __tablename__ = "recurring_transactions"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_recurring_amount_nonneg"),
        CheckConstraint(
            "(to_account_id IS NULL) OR (account_id <> to_account_id)",
            name="ck_recurring_not_self_transfer",
        ),
        Index("ix_recurring_user_next_run", "user_id", "next_run_date"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transaction_kinds.id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    to_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=True,
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    cadence: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # weekly/monthly/etc.
    next_run_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    user: Mapped[User] = relationship(back_populates="recurring")
    kind: Mapped[TransactionKind] = relationship(back_populates="recurring")

    account: Mapped[Account] = relationship(foreign_keys=[account_id])
    to_account: Mapped[Account | None] = relationship(foreign_keys=[to_account_id])
    category: Mapped[Category | None] = relationship(foreign_keys=[category_id])
