# schemas/transactions.py
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .common import APIModel, ReadBase

# If you prefer: kind_id only. If you prefer convenience in the API: kind_name.
# Since you have lookup-table "transaction_kinds", both patterns are common.
KindName = Literal["income", "expense", "transfer", "refund"]


class TransactionCreate(APIModel):
    # Prefer kind_name for client UX; server resolves to kind_id
    kind: KindName

    account_id: uuid.UUID
    to_account_id: uuid.UUID | None = None  # required for transfers
    category_id: uuid.UUID | None = None  # usually null for transfer

    amount: Decimal = Field(ge=0)
    description: str | None = None
    posted_at: date

    refunded_transaction_id: uuid.UUID | None = None
    transfer_group_id: uuid.UUID | None = None  # server can generate if omitted
    budget_item_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_business_rules(self):
        if self.kind == "transfer":
            if self.to_account_id is None:
                raise ValueError("to_account_id is required for transfer transactions.")
            if self.category_id is not None:
                raise ValueError("category_id should be null for transfers.")
            if self.account_id == self.to_account_id:
                raise ValueError("account_id and to_account_id cannot be the same.")
        # non-transfer: destination account should generally be null
        # (keep it strict; relax if you want)
        elif self.to_account_id is not None:
            raise ValueError("to_account_id must be null for non-transfer transactions.")
        if self.kind == "refund" and self.refunded_transaction_id is None:
            # You can relax this if you want refunds without linkage
            raise ValueError("refunded_transaction_id is required for refund transactions.")
        return self


class TransactionUpdate(APIModel):
    # Typically you allow editing description/category/posted_at/amount etc.
    category_id: uuid.UUID | None = None
    description: str | None = None
    posted_at: date | None = None
    amount: Decimal | None = Field(default=None, ge=0)

    # For transfers you may allow editing to_account_id
    to_account_id: uuid.UUID | None = None

    budget_item_id: uuid.UUID | None = None
    is_active: bool | None = None


class TransactionRead(ReadBase):
    user_id: uuid.UUID

    kind_id: uuid.UUID
    kind: str

    account_id: uuid.UUID
    account_name: str
    to_account_id: uuid.UUID | None = None
    to_account_name: str | None = None
    category_id: uuid.UUID | None = None
    category_name: str | None = None

    amount: Decimal
    description: str | None = None
    posted_at: date

    refunded_transaction_id: uuid.UUID | None = None
    transfer_group_id: uuid.UUID | None = None

    budget_item_id: uuid.UUID | None = None
    budget_item_name: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _resolve_names(cls, data):
        if isinstance(data, dict):
            return data
        bi = data.budget_item
        if bi:
            budget_item_name = bi.name or bi.category.name if bi.category else None
        else:
            budget_item_name = None
        return {
            "id": data.id,
            "created_at": data.created_at,
            "updated_at": data.updated_at,
            "is_active": data.is_active,
            "user_id": data.user_id,
            "kind_id": data.kind_id,
            "kind": data.kind.name if data.kind else "",
            "account_id": data.account_id,
            "account_name": data.account.name if data.account else "",
            "to_account_id": data.to_account_id,
            "to_account_name": data.to_account.name if data.to_account else None,
            "category_id": data.category_id,
            "category_name": data.category.name if data.category else None,
            "amount": data.amount,
            "description": data.description,
            "posted_at": data.posted_at,
            "refunded_transaction_id": data.refunded_transaction_id,
            "transfer_group_id": data.transfer_group_id,
            "budget_item_id": data.budget_item_id,
            "budget_item_name": budget_item_name,
        }
