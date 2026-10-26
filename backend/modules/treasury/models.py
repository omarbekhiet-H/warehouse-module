# -*- coding: utf-8 -*-
"""قسم الخزينة والمالية — الجداول.

يدور حول ثلاث دورات رئيسية وفق الأصول المهنية:
1) الصناديق والبنوك (Cash & Bank Accounts) مع وردية أمين الخزينة (Shift).
2) سندات القبض والصرف بأنواعها (نقدية/شيك/بطاقة/تحويل) مع الترحيل الآلي للأستاذ العام.
3) دورة الشيكات الواردة والصادرة بمراحلها (تسجيل → إيداع/حفظ → تحصيل/صرف → ارتداد/إلغاء)،
   وحسابات وسيطة (شيكات تحت التحصيل / أوراق دفع) تفصل بين الالتزام القانوني والحركة البنكية الفعلية.
4) التسويات البنكية (Bank Reconciliation) بمبدأ المربع المعدل:
   رصيد البنك الدفتري ← تعديلات الشركة = رصيد البنك الفعلي ← تعديلات البنك.

ملاحظة ربط: لا يوجد FK عابر للأقسام؛ الأرقام المرجعية للمستندات (invoice_no/receipt_no)
تُخزن كنص، والتكامل يتم عبر طبقة الخدمات فقط.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class CashAccount(Base):
    """صندوق أو حساب بنكي — لكل منها حساب أستاذ مرتبط (ربط منطقي برقم الحساب)."""
    __tablename__ = "tre_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name_ar: Mapped[str] = mapped_column(String(128))
    account_kind: Mapped[str] = mapped_column(String(16), default="cash")  # cash/bank
    bank_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    iban: Mapped[str | None] = mapped_column(String(34), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="EGP")
    gl_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1100 نقديًا / 1110 بنكًا
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class CashierShift(Base):
    """وردية أمين خزينة — لرقابة إغلاق الصندوق ومطابقة النقدية الفعلية بالدفترية."""
    __tablename__ = "tre_shifts"

    id: Mapped[int] = mapped_column(primary_key=True)
    shift_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    cash_account_id: Mapped[int] = mapped_column(ForeignKey("tre_accounts.id"), index=True)
    cashier: Mapped[str] = mapped_column(String(64))
    opening_float: Mapped[float] = mapped_column(Numeric(18, 4), default=0)  # عهدة فتح الوردية
    opened_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open/closed
    expected_cash: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    counted_cash: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    variance: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    cash_account: Mapped[CashAccount] = relationship()


class Voucher(Base):
    """سند قبض أو صرف — رأس الدورة المستندية؛ نوع السداد يحدد القيود والملاحق."""
    __tablename__ = "tre_vouchers"

    id: Mapped[int] = mapped_column(primary_key=True)
    voucher_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    direction: Mapped[str] = mapped_column(String(8))                     # receipt/payment
    pay_method: Mapped[str] = mapped_column(String(16))                   # cash/check/card/bank_transfer
    amount: Mapped[float] = mapped_column(Numeric(18, 4))                 # إجمالي قيمة السند
    fee_amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0)  # عمولة بطاقة/مصاريف بنكية
    net_amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0)  # الصافي بعد العمولة
    date: Mapped[dt.date] = mapped_column(DateTime)
    party_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # customer/supplier/other
    party_id: Mapped[int | None] = mapped_column(Integer, nullable=True)       # ربط منطقي بقسم المبيعات/المشتريات
    party_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    counterparty_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)  # رقم فاتورة الطرف الآخر
    source_module: Mapped[str | None] = mapped_column(String(32), nullable=True)     # sales/purchasing/manual
    source_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)        # مستند داخلي مربوط (طلب صرف...)
    account_id: Mapped[int] = mapped_column(ForeignKey("tre_accounts.id"), index=True)  # الصندوق/الحساب البنكي
    shift_id: Mapped[int | None] = mapped_column(ForeignKey("tre_shifts.id"), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="posted")  # draft/posted/cancelled
    journal_entry_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # القيد الناتج (منع الازدواج)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    account: Mapped[CashAccount] = relationship()
    lines: Mapped[list["VoucherAllocation"]] = relationship(
        back_populates="voucher", cascade="all, delete-orphan")
    check: Mapped["Check | None"] = relationship(back_populates="voucher", uselist=False)


class VoucherAllocation(Base):
    """توزيع قيمة السند على فواتير مفتوحة (FIFO) — لإغلاق/تقليل مستحقات العملاء والموردين."""
    __tablename__ = "tre_voucher_allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    voucher_id: Mapped[int] = mapped_column(ForeignKey("tre_vouchers.id"), index=True)
    invoice_ref: Mapped[str] = mapped_column(String(64))  # رقم المستند في قسم المبيعات/المشتريات
    allocated_amount: Mapped[float] = mapped_column(Numeric(18, 4))

    voucher: Mapped[Voucher] = relationship(back_populates="lines")


class Check(Base):
    """الشيك — كيان مستقل بدورة حياة كاملة (وارده/صادر) موصول بسنده المحاسبي."""
    __tablename__ = "tre_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    check_no: Mapped[str] = mapped_column(String(64), index=True)
    direction: Mapped[str] = mapped_column(String(8))  # in/out
    amount: Mapped[float] = mapped_column(Numeric(18, 4))
    bank_name: Mapped[str] = mapped_column(String(128))
    issue_date: Mapped[dt.date] = mapped_column(DateTime)      # تاريخ الاستحقاق/التحرير
    deposit_date: Mapped[dt.date | None] = mapped_column(DateTime, nullable=True)
    clearance_date: Mapped[dt.date | None] = mapped_column(DateTime, nullable=True)
    party_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    party_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    party_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    voucher_id: Mapped[int | None] = mapped_column(ForeignKey("tre_vouchers.id"), nullable=True)
    collection_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("tre_check_batches.id"), nullable=True, index=True)
    payment_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("tre_payment_runs.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="registered")
    # in:  registered → deposited → cleared → bounced/cancelled
    # out: issued → saved → delivered → cleared → returned/cancelled
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_path: Mapped[str | None] = mapped_column(String(255), nullable=True)  # صورة الشيك المسحوبة
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    voucher: Mapped[Voucher | None] = relationship(back_populates="check")


class CheckBatch(Base):
    """حافظة تحصيل شيكات واردة — تُرسل للبنك ثم تُقبَّض عند إشعار البنك."""
    __tablename__ = "tre_check_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    bank_account_id: Mapped[int] = mapped_column(ForeignKey("tre_accounts.id"), index=True)
    total_amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    checks_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open/submitted/settled
    submitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    settled_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    bank_account: Mapped[CashAccount] = relationship()


class PaymentRun(Base):
    """دفتر صرف شيكات صادرة — يُنشئ سندات الصرف بقيودها لحظة الإصدار."""
    __tablename__ = "tre_payment_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    bank_account_id: Mapped[int] = mapped_column(ForeignKey("tre_accounts.id"), index=True)
    total_amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    checks_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="issued")  # issued/cleared/cancelled
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    bank_account: Mapped[CashAccount] = relationship()


class BankStatementLine(Base):
    """سطر كشف حساب بنكي مستورد — يغذّي مطابقة التسوية البنكية."""
    __tablename__ = "tre_bank_statement_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(ForeignKey("tre_accounts.id"), index=True)
    statement_date: Mapped[dt.date] = mapped_column(DateTime)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    debit: Mapped[float] = mapped_column(Numeric(18, 4), default=0)   # سحب من البنك
    credit: Mapped[float] = mapped_column(Numeric(18, 4), default=0)  # إيداع لدى البنك
    reconciled: Mapped[bool] = mapped_column(default=False)
    reconciliation_id: Mapped[int | None] = mapped_column(
        ForeignKey("tre_reconciliations.id"), nullable=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    bank_account: Mapped[CashAccount] = relationship()


class Reconciliation(Base):
    """تسوية بنكية — مربع تسوية وفق المعيار: دفتري ± تعديلات الشركة = فعلي ± تعديلات البنك."""
    __tablename__ = "tre_reconciliations"

    id: Mapped[int] = mapped_column(primary_key=True)
    recon_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    bank_account_id: Mapped[int] = mapped_column(ForeignKey("tre_accounts.id"), index=True)
    period_from: Mapped[dt.date] = mapped_column(DateTime)
    period_to: Mapped[dt.date] = mapped_column(DateTime)
    book_balance: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    statement_balance: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    company_adjustments_total: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    bank_adjustments_total: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    adjusted_book_balance: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    adjusted_statement_balance: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    is_balanced: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/finalized
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    bank_account: Mapped[CashAccount] = relationship()
    items: Mapped[list["ReconciliationItem"]] = relationship(
        back_populates="reconciliation", cascade="all, delete-orphan")


class ReconciliationItem(Base):
    """بند تسوية: مصروفات بنكية/إشعارات مدينة (تعديل على الدفاتر ⇒ قيود)،
    شيكات معلقة وإيداعات بالطريق (تعديل على كشف البنك ⇒ بدون قيود)."""
    __tablename__ = "tre_reconciliation_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    reconciliation_id: Mapped[int] = mapped_column(ForeignKey("tre_reconciliations.id"), index=True)
    item_type: Mapped[str] = mapped_column(String(32))
    # bank_charge / direct_debit_note / interest_income / outstanding_check / deposit_in_transit / other
    side: Mapped[str] = mapped_column(String(16))  # book / statement
    amount: Mapped[float] = mapped_column(Numeric(18, 4))
    sign: Mapped[str] = mapped_column(String(8), default="+")  # + / -
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_journal: Mapped[bool] = mapped_column(default=False)
    journal_entry_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    reconciliation: Mapped[Reconciliation] = relationship(back_populates="items")
