# -*- coding: utf-8 -*-
"""قسم المشتريات — مخططات Pydantic."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


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


# ---------- طلب الاحتياجات (Material Requisition) ----------
class MRLineIn(BaseModel):
    item_id: int
    qty: float = Field(gt=0)
    remarks: str | None = None


class MRCreate(BaseModel):
    requested_by: str | None = None
    department_id: int | None = None
    department_name: str | None = None
    warehouse_id: int | None = None
    reason: str | None = None
    remarks: str | None = None
    lines: list[MRLineIn] = Field(min_length=1)


class MRLineOut(MRLineIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    converted_qty: float


class MROut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    mr_no: str
    date: dt.datetime
    requested_by: str | None
    department_id: int | None
    department_name: str | None
    warehouse_id: int | None
    reason: str | None
    status: str
    approved_by: str | None
    approved_at: dt.datetime | None
    remarks: str | None
    created_by: str | None
    lines: list[MRLineOut]


class MRDecisionIn(BaseModel):
    """قرار الاعتماد/الرفض — مع سبب الرفض اختياريًا."""
    reason: str | None = None


# ---------- أوامر الشراء ----------
class POLineIn(BaseModel):
    item_id: int
    qty: float = Field(gt=0)
    unit_cost: float = Field(ge=0, default=0)
    mr_line_id: int | None = None   # عند التحويل من طلب احتياجات


class POCreate(BaseModel):
    supplier_id: int
    warehouse_id: int | None = None
    mr_id: int | None = None         # طلب الاحتياجات المصدر (اختياري)
    expected_date: dt.date | None = None
    payment_terms: str | None = None
    remarks: str | None = None
    lines: list[POLineIn] = Field(min_length=1)


class POFromMRIn(BaseModel):
    """تحويل كامل لطلب احتياجات معتمد إلى أمر شراء (سحب تلقائي للكميات)."""
    supplier_id: int
    warehouse_id: int | None = None
    expected_date: dt.date | None = None
    payment_terms: str | None = None
    unit_costs: dict[int, float] | None = None  # {mr_line_id: سعر تفاوضي} — وإلا يُستخدم سعر الصنف


class POLineOut(POLineIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    mr_line_id: int | None = None
    received_qty: float
    billed_qty: float


class POOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    po_no: str
    supplier_id: int
    warehouse_id: int | None
    mr_id: int | None = None
    payment_terms: str | None = None
    date: dt.datetime
    expected_date: dt.date | None
    total: float
    status: str
    remarks: str | None
    created_by: str | None
    lines: list[POLineOut]


# ---------- إذن التوريد الوارد من المورد (Delivery Note) ----------
class DNLineIn(BaseModel):
    po_line_id: int
    qty_declared: float = Field(gt=0)


class DNCreate(BaseModel):
    po_id: int
    supplier_dn_no: str | None = None
    vehicle: str | None = None
    remarks: str | None = None
    lines: list[DNLineIn] = Field(min_length=1)


class DNLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    po_line_id: int
    item_id: int
    qty_declared: float
    qty_accepted: float
    qty_rejected: float


class DNOout(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    dn_no: str
    supplier_dn_no: str | None
    po_id: int
    date: dt.datetime
    vehicle: str | None
    declared_qty: float
    status: str
    remarks: str | None
    created_by: str | None
    lines: list[DNLineOut]


# ---------- أذون الاستلام ----------
class ReceiptLineIn(BaseModel):
    """سطر استلام: الكمية المقبولة بعد الفحص النوعي + المرفوضة (لا تدخل المخزون)."""
    po_line_id: int
    qty_accepted: float = Field(ge=0)
    qty_rejected: float = Field(ge=0, default=0)
    unit_cost: float | None = None        # افتراضيًا تكلفة سطر أمر الشراء

    @model_validator(mode="before")
    @classmethod
    def _compat_qty(cls, data):
        """توافق خلفي: من يرسل 'qty' فقط تُعتبر الكمية المقبولة."""
        if isinstance(data, dict) and "qty" in data and "qty_accepted" not in data:
            data = dict(data)
            data["qty_accepted"] = data.pop("qty")
        return data


class ReceiptCreate(BaseModel):
    po_id: int
    warehouse_id: int
    dn_id: int | None = None              # إذن التوريد المرتبط (اختياري — للسحب التلقائي)
    inspector: str | None = None          # أمين المستودع/الفاحص
    remarks: str | None = None
    supplier_invoice_ref: str | None = None   # رقم الفاتورة الضريبية للمورد (للمطابقة الثلاثية)
    lines: list[ReceiptLineIn] = Field(min_length=1)


class ReceiptLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    po_line_id: int
    item_id: int
    qty: float          # المقبولة فعليًا (الحقل المخزني)
    qty_rejected: float = 0
    unit_cost: float


class ReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    receipt_no: str
    po_id: int
    dn_id: int | None = None
    warehouse_id: int
    date: dt.datetime
    invoice_id: int | None
    supplier_invoice_ref: str | None = None
    inspector: str | None = None
    status: str
    remarks: str | None
    created_by: str | None
    lines: list[ReceiptLineOut]


class PurchaseLineOut(PurchaseLineIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    line_total: float


class PurchaseCreate(BaseModel):
    """فاتورة شراء. عند الربط بـ po_id/grn_id تُسحب الكميات تلقائيًا من إذن الاستلام إن لم تُرسل الأسطر."""
    supplier_id: int
    warehouse_id: int
    po_id: int | None = None   # أمر الشراء المرتبط (اختياري — فاتورة مباشرة)
    grn_id: int | None = None  # إذن الاستلام المرتبط (المصدر الأساسي للكميات المفوترة)
    discount: float = Field(ge=0, default=0)
    freight_charges: float = Field(ge=0, default=0)   # شحن/نقل — يوزَّع على تكلفة الأصناف (IAS 2)
    customs_charges: float = Field(ge=0, default=0)   # جمارك ومشتقات إضافية
    tax_rate: float = Field(ge=0, le=100, default=0)
    remarks: str | None = None
    lines: list[PurchaseLineIn] = Field(default_factory=list)  # اختياري عند السحب من GRN


class PurchaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    invoice_no: str
    supplier_id: int
    po_id: int | None = None
    grn_id: int | None = None
    date: dt.datetime
    subtotal: float
    discount: float
    freight_charges: float = 0
    customs_charges: float = 0
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
