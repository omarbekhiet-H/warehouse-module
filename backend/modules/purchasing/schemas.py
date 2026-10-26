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
    warehouse_id: int          # المخزن الذي تُضاف إليه البضاعة
    po_id: int | None = None   # أمر الشراء المرتبط (اختياري — فاتورة مباشرة)
    discount: float = Field(ge=0, default=0)
    tax_rate: float = Field(ge=0, le=100, default=0)
    remarks: str | None = None
    lines: list[PurchaseLineIn] = Field(min_length=1)


# ---------- أوامر الشراء ----------
class POLineIn(BaseModel):
    item_id: int
    qty: float = Field(gt=0)
    unit_cost: float = Field(ge=0, default=0)


class POCreate(BaseModel):
    supplier_id: int
    warehouse_id: int | None = None
    expected_date: dt.date | None = None
    remarks: str | None = None
    lines: list[POLineIn] = Field(min_length=1)


class POLineOut(POLineIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    received_qty: float


class POOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    po_no: str
    supplier_id: int
    warehouse_id: int | None
    date: dt.datetime
    expected_date: dt.date | None
    total: float
    status: str
    remarks: str | None
    created_by: str | None
    lines: list[POLineOut]


# ---------- أذون الاستلام ----------
class ReceiptLineIn(BaseModel):
    po_line_id: int
    qty: float = Field(gt=0)
    unit_cost: float | None = None  # افتراضيًا تكلفة سطر أمر الشراء


class ReceiptCreate(BaseModel):
    po_id: int
    warehouse_id: int
    remarks: str | None = None
    supplier_invoice_ref: str | None = None   # رقم الفاتورة الضريبية للمورد (للمطابقة الثلاثية)
    lines: list[ReceiptLineIn] = Field(min_length=1)


class ReceiptLineOut(ReceiptLineIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    item_id: int


class ReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    receipt_no: str
    po_id: int
    warehouse_id: int
    date: dt.datetime
    invoice_id: int | None
    supplier_invoice_ref: str | None = None
    status: str
    remarks: str | None
    created_by: str | None
    lines: list[ReceiptLineOut]


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
