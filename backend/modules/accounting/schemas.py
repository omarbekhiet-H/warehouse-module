# -*- coding: utf-8 -*-
"""قسم المحاسبة — مخططات Pydantic (إدخال/إخراج)."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AccountBase(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name_ar: str = Field(min_length=1, max_length=128)
    name_en: str | None = None
    account_type: Literal["asset", "liability", "equity", "revenue", "expense"]
    parent_id: int | None = None
    is_active: bool = True
    notes: str | None = None


class AccountCreate(AccountBase):
    pass


class AccountUpdate(BaseModel):
    name_ar: str | None = None
    name_en: str | None = None
    account_type: Literal["asset", "liability", "equity", "revenue", "expense"] | None = None
    parent_id: int | None = None
    is_active: bool | None = None
    notes: str | None = None


class AccountOut(AccountBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: dt.datetime


class JournalLineIn(BaseModel):
    account_id: int
    debit: float = Field(ge=0, default=0)
    credit: float = Field(ge=0, default=0)


class JournalEntryCreate(BaseModel):
    date: dt.date
    description: str | None = None
    lines: list[JournalLineIn] = Field(min_length=2)


class JournalLineOut(JournalLineIn):
    model_config = ConfigDict(from_attributes=True)

    id: int


class JournalEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entry_no: str
    date: dt.date
    description: str | None
    source_module: str | None
    source_ref: str | None
    created_by: str | None
    lines: list[JournalLineOut]
