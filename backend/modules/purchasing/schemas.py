# -*- coding: utf-8 -*-
"""قسم المشتريات — مخططات Pydantic."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class SupplierBase(BaseModel):
    name_ar: str = Field(min_length=1, max_length=255)
    phone: str | None = None
    email: EmailStr | None = None
    tax_number: str | None = None
    payable_account_id: int | None = None  # مطابق لاسم الحقل في models.Supplier
    is_active: bool = True


class SupplierCreate(SupplierBase):
    pass


class SupplierOut(SupplierBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class PurchaseLineIn(BaseModel):
    item_id: int
    qty: float = Field(gt=0)
    unit_cost: float = Field(ge=0)


class PurchaseCreate(BaseModel):
    supplier_id: int
    warehouse_id: int          # المخزن الذي تُخصم منه البضاعة
    discount: float = Field(ge=0, default=0)
    tax_rate: float = Field(ge=0, le=100, default=0)
    remarks: str | None = None
    lines: list[PurchaseLineIn] = Field(min_length=1)


class PurchaseLineOut(PurchaseLineIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    line_total: float


class PurchaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    invoice_no: str
    supplier_id: int
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
    lines: list[PurchaseLineOut]


class PaymentIn(BaseModel):
    amount: float = Field(gt=0)
