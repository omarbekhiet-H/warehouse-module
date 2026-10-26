# -*- coding: utf-8 -*-
"""قسم المبيعات — مخططات Pydantic."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CustomerBase(BaseModel):
    name_ar: str = Field(min_length=1, max_length=255)
    phone: str | None = None
    email: EmailStr | None = None
    address: str | None = None
    tax_number: str | None = None
    receivable_account_id: int | None = None
    is_active: bool = True


class CustomerCreate(CustomerBase):
    pass


class CustomerOut(CustomerBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class InvoiceLineIn(BaseModel):
    item_id: int
    qty: float = Field(gt=0)
    unit_price: float = Field(ge=0)


class InvoiceCreate(BaseModel):
    customer_id: int
    warehouse_id: int          # المخزن الذي تُخصم منه البضاعة
    discount: float = Field(ge=0, default=0)
    tax_rate: float = Field(ge=0, le=100, default=0)
    remarks: str | None = None
    lines: list[InvoiceLineIn] = Field(min_length=1)


class InvoiceLineOut(InvoiceLineIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    line_total: float


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    invoice_no: str
    customer_id: int
    date: dt.datetime
    subtotal: float
    discount: float
    tax_rate: float
    tax_amount: float
    total: float
    paid_amount: float
    status: str
    remarks: str | None
    created_by: str | None
    lines: list[InvoiceLineOut]


class PaymentIn(BaseModel):
    amount: float = Field(gt=0)
