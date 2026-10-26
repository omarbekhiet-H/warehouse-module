# -*- coding: utf-8 -*-
"""قسم الخزينة والمالية — مخططات Pydantic."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------- الصناديق/البنوك ----------
class CashAccountBase(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name_ar: str = Field(min_length=1, max_length=128)
    account_kind: Literal["cash", "bank"] = "cash"
    bank_name: str | None = None
    iban: str | None = None
    currency: str = "EGP"
    gl_account_id: int | None = None
    is_active: bool = True


class CashAccountCreate(CashAccountBase):
    pass


class CashAccountOut(CashAccountBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: dt.datetime


# ---------- سندات القبض/الصرف ----------
class VoucherCreate(BaseModel):
    direction: Literal["receipt", "payment"]
    pay_method: Literal["cash", "check", "card", "bank_transfer"]
    amount: float = Field(gt=0)
    fee_amount: float = Field(ge=0, default=0)          # عمولة بطاقة / مصاريف بنكية
    date: dt.date | None = None                          # تُفتراض اليوم إن لم تُرسل
    party_type: Literal["customer", "supplier", "other"] | None = None
    party_id: int | None = None
    counterparty_ref: str | None = None                  # رقم فاتورة العميل/المورد (إن وُجدت)
    source_module: str | None = None                     # manual/expense...
    source_ref: str | None = None                        # رقم طلب الصرف المعتمد مثلًا
    expense_account_id: int | None = None                # للمصروفات النثرية بلا طرف ملزم
    account_id: int                                      # الصندوق أو الحساب البنكي
    shift_id: int | None = None                          # وردية أمين الخزينة (نقدي)
    description: str | None = None
    remarks: str | None = None
    check_data: "CheckIn | None" = None                  # بيانات الشيك إن كان السداد بشيك
    card_data: "CardIn | None" = None                    # بيانات العملية إن كانت ببطاقة


class CheckIn(BaseModel):
    check_no: str = Field(min_length=1, max_length=64)
    bank_name: str = Field(min_length=1, max_length=128)
    issue_date: dt.date                                  # تاريخ الاستحقاق
    image_path: str | None = None


class CardIn(BaseModel):
    auth_code: str | None = None                         # رقم التفويض
    reference_no: str | None = None                      # المرجع البنكي/POS
    terminal_id: str | None = None
    settlement_date: dt.date | None = None               # تاريخ التسوية المتوقع من البنك


class AllocationIn(BaseModel):
    invoice_ref: str
    allocated_amount: float = Field(gt=0)


class AllocationOut(AllocationIn):
    model_config = ConfigDict(from_attributes=True)


class VoucherOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    voucher_no: str
    direction: str
    pay_method: str
    amount: float
    fee_amount: float
    net_amount: float
    date: dt.date
    party_type: str | None
    party_id: int | None
    party_name: str | None
    counterparty_ref: str | None
    source_module: str | None
    source_ref: str | None
    account_id: int
    shift_id: int | None
    description: str | None
    status: str
    journal_entry_id: int | None
    created_by: str | None
    lines: list[AllocationOut] = []


# ---------- الوردية ----------
class ShiftOpen(BaseModel):
    cash_account_id: int
    cashier: str = Field(min_length=1, max_length=64)
    opening_float: float = Field(ge=0, default=0)
    notes: str | None = None


class ShiftClose(BaseModel):
    counted_cash: float = Field(ge=0)
    notes: str | None = None


class ShiftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    shift_no: str
    cash_account_id: int
    cashier: str
    opening_float: float
    opened_at: dt.datetime
    closed_at: dt.datetime | None
    status: str
    expected_cash: float | None
    counted_cash: float | None
    variance: float | None
    notes: str | None


# ---------- الشيكات ودوراتها ----------
class IncomingCheckRegister(BaseModel):
    """تسجيل شيك وارد من عميل (سواء مع سند قبض أو مستقل)."""
    check_no: str
    bank_name: str
    amount: float = Field(gt=0)
    issue_date: dt.date
    customer_id: int | None = None
    customer_name: str | None = None
    invoice_ref: str | None = None
    image_path: str | None = None
    remarks: str | None = None


class BatchCreate(BaseModel):
    bank_account_id: int
    check_ids: list[int] = Field(min_length=1)
    remarks: str | None = None


class BatchSettle(BaseModel):
    settled_date: dt.date | None = None
    bounced_check_ids: list[int] = []       # شيكات رفضها البنك ضمن الحافظة
    bank_fees_total: float = Field(ge=0, default=0)


class OutgoingCheckIssue(BaseModel):
    supplier_id: int | None = None
    supplier_name: str | None = None
    check_no: str
    bank_name: str
    amount: float = Field(gt=0)
    issue_date: dt.date
    invoice_ref: str | None = None
    remarks: str | None = None


class PaymentRunCreate(BaseModel):
    bank_account_id: int
    checks: list[OutgoingCheckIssue] = Field(min_length=1)
    remarks: str | None = None


class CheckAction(BaseModel):
    action: Literal["deposit", "clear", "bounce", "deliver", "return", "cancel"]
    reason: str | None = None
    bank_fee: float = Field(ge=0, default=0)   # رسوم بنك عند الارتداد/الإلغاء


class CheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    check_no: str
    direction: str
    amount: float
    bank_name: str
    issue_date: dt.date
    deposit_date: dt.date | None
    clearance_date: dt.date | None
    party_type: str | None
    party_id: int | None
    party_name: str | None
    voucher_id: int | None
    collection_batch_id: int | None
    payment_run_id: int | None
    status: str
    reject_reason: str | None
    image_path: str | None


# ---------- التسويات البنكية ----------
class StatementLineIn(BaseModel):
    statement_date: dt.date
    description: str | None = None
    debit: float = Field(ge=0, default=0)
    credit: float = Field(ge=0, default=0)


class ReconItemIn(BaseModel):
    item_type: Literal["bank_charge", "direct_debit_note", "interest_income",
                       "outstanding_check", "deposit_in_transit", "other"]
    side: Literal["book", "statement"]
    amount: float = Field(gt=0)
    sign: Literal["+", "-"] = "+"
    reference: str | None = None
    description: str | None = None


class ReconciliationCreate(BaseModel):
    bank_account_id: int
    period_from: dt.date
    period_to: dt.date
    statement_balance: float
    items: list[ReconItemIn] = []
    remarks: str | None = None


class ReconciliationFinalize(BaseModel):
    post_missing_journals: bool = True   # ترحيل بنود الدفاتر غير المسجلة


class ReconItemOut(ReconItemIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    requires_journal: bool
    journal_entry_id: int | None


class ReconciliationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    recon_no: str
    bank_account_id: int
    period_from: dt.date
    period_to: dt.date
    book_balance: float
    statement_balance: float
    company_adjustments_total: float
    bank_adjustments_total: float
    adjusted_book_balance: float
    adjusted_statement_balance: float
    is_balanced: bool
    status: str
    remarks: str | None
    items: list[ReconItemOut] = []


VoucherCreate.model_rebuild()
