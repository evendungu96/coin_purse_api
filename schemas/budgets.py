# schemas/budgets.py
from __future__ import annotations

import calendar
import uuid
from datetime import date
from decimal import Decimal

from pydantic import Field, model_validator

from .common import APIModel, ReadBase


class BudgetCreate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    period_start: date
    period_end: date

    @model_validator(mode="after")
    def validate_period(self):
        if self.period_end < self.period_start:
            raise ValueError("period_end must be >= period_start")
        if not self.name:
            self.name = f"{calendar.month_name[self.period_start.month]} {self.period_start.year}"
        return self


class BudgetUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    period_start: date | None = None
    period_end: date | None = None
    is_active: bool | None = None
    is_template: bool | None = None

    @model_validator(mode="after")
    def validate_period(self):
        if (
            self.period_start
            and self.period_end
            and self.period_end < self.period_start
        ):
            raise ValueError("period_end must be >= period_start")
        return self


class BudgetRead(ReadBase):
    user_id: uuid.UUID
    name: str
    period_start: date
    period_end: date
    is_template: bool = False
    source_budget_id: uuid.UUID | None = None


class BudgetClone(APIModel):
    period_start: date
    period_end: date
    name: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_period(self):
        if self.period_end < self.period_start:
            raise ValueError("period_end must be >= period_start")
        if not self.name:
            self.name = f"{calendar.month_name[self.period_start.month]} {self.period_start.year}"
        return self


class BudgetItemCreate(APIModel):
    category_id: uuid.UUID
    limit_amount: Decimal = Field(ge=0)
    name: str | None = Field(default=None, max_length=200)


class BudgetItemUpdate(APIModel):
    category_id: uuid.UUID | None = None
    limit_amount: Decimal | None = Field(default=None, ge=0)
    name: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None


class BudgetItemRead(ReadBase):
    budget_id: uuid.UUID
    budget_name: str
    category_id: uuid.UUID
    category_name: str
    limit_amount: Decimal
    name: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _resolve_names(cls, data):
        if isinstance(data, dict):
            return data
        return {
            "id": data.id,
            "created_at": data.created_at,
            "updated_at": data.updated_at,
            "is_active": data.is_active,
            "budget_id": data.budget_id,
            "budget_name": data.budget.name if data.budget else "",
            "category_id": data.category_id,
            "category_name": data.category.name if data.category else "",
            "limit_amount": data.limit_amount,
            "name": data.name,
        }
