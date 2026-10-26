# -*- coding: utf-8 -*-
"""قسم المشتريات — الجداول (موردون / فواتير شراء).

التكامل: فاتورة الشراء ⇒ حركة مخزون واردة (عبر services المخازن)
⇒ قيد محاسبي تلقائي (عبر services المحاسبة).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class Supplier(Base):
    __tablename__ = "pur_suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name_ar: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(128))
    tax_number: Mapped[str | None] = mapped_column(String(32))
    payable_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ربط منطقي بالمحاسبة
    is_active: Mapped[bool] = mapped_column(default=True)


class MaterialRequest(Base):
    """طلب الاحتياجات — نقطة بداية دورة المشتريات (مستند داخلي بلا أثر مخزني/محاسبي).

    الحالات: draft -> approved / rejected. المعتمد فقط يُحوَّل لأمر شراء.
    """
    __tablename__ = "pur_material_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    mr_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    date: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    requested_by: Mapped[str | None] = mapped_column(String(128))   # اسم الطالب/المسؤول
    department_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ربط منطقي بقسم HR (بدون FK عابر للأقسام)
    department_name: Mapped[str | None] = mapped_column(String(128))
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("inv_warehouses.id"), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text)                # سبب الطلب (نقص رصيد / خطة عمل)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/approved/rejected/closed
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(64))

    lines: Mapped[list["MaterialRequestLine"]] = relationship(back_populates="request",
                                                              cascade="all, delete-orphan")


class MaterialRequestLine(Base):
    __tablename__ = "pur_material_request_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    mr_id: Mapped[int] = mapped_column(ForeignKey("pur_material_requests.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inv_items.id"), index=True)
    qty: Mapped[float] = mapped_column(Numeric(18, 4))
    converted_qty: Mapped[float] = mapped_column(Numeric(18, 4), default=0)  # ما حُوّل فعليًا لأوامر شراء
    remarks: Mapped[str | None] = mapped_column(String(255))

    request: Mapped[MaterialRequest] = relationship(back_populates="lines")


class PurchaseOrder(Base):
    """أمر شراء — المرحلة الأولى في دورة المشتريات، يُستلم تحته إذن استلام."""
    __tablename__ = "pur_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("pur_suppliers.id"), index=True)
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("inv_warehouses.id"), nullable=True)
    mr_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ربط منطقي بطلب الاحتياجات (تتبع المصدر)
    payment_terms: Mapped[str | None] = mapped_column(String(128))     # شروط السداد (آجل 30 يوم... إلخ)
    date: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    expected_date: Mapped[dt.date | None] = mapped_column(DateTime, nullable=True)
    total: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    # draft -> approved -> sent -> partially_received -> received -> closed / cancelled
    status: Mapped[str] = mapped_column(String(24), default="draft")
    remarks: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(64))

    supplier: Mapped[Supplier] = relationship()
    lines: Mapped[list["PurchaseOrderLine"]] = relationship(back_populates="order",
                                                            cascade="all, delete-orphan")


class PurchaseOrderLine(Base):
    __tablename__ = "pur_order_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("pur_orders.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inv_items.id"), index=True)
    mr_line_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # تتبع البند من طلب الاحتياجات
    qty: Mapped[float] = mapped_column(Numeric(18, 4))
    unit_cost: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    received_qty: Mapped[float] = mapped_column(Numeric(18, 4), default=0)  # كان مفقودًا (لا تتبع للاستلام)
    billed_qty: Mapped[float] = mapped_column(Numeric(18, 4), default=0)    # الكميات المفوترة (للمطابقة الثلاثية)

    order: Mapped[PurchaseOrder] = relationship(back_populates="lines")


class DeliveryNote(Base):
    """إذن التوريد الوارد من المورد (وصل الإشعار قبل الفحص والاستلام المخزني الرسمي).

    يرقّب الكميات المعلنة من المورد أمام أمر الشراء للتحقق والسيطرة، ثم يُستدعى
    رقمه في إذن الاستلام (GRN) لإتمام المطابقة دون إدخال يدوي مكرر.
    """
    __tablename__ = "pur_delivery_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    dn_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)      # رقمنا الداخلي
    supplier_dn_no: Mapped[str | None] = mapped_column(String(64))               # رقم الإذن لدى المورد
    po_id: Mapped[int] = mapped_column(ForeignKey("pur_orders.id"), index=True)
    date: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    vehicle: Mapped[str | None] = mapped_column(String(64))                      # وسيلة النقل/لوحة السيارة
    declared_qty: Mapped[float] = mapped_column(Numeric(18, 4), default=0)       # إجمالي الكمية الواردة المعلن عنها
    status: Mapped[str] = mapped_column(String(16), default="pending")           # pending/received/partially_received/rejected
    remarks: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(64))

    order: Mapped[PurchaseOrder] = relationship()
    lines: Mapped[list["DeliveryNoteLine"]] = relationship(back_populates="note",
                                                           cascade="all, delete-orphan")


class DeliveryNoteLine(Base):
    __tablename__ = "pur_delivery_note_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    dn_id: Mapped[int] = mapped_column(ForeignKey("pur_delivery_notes.id"), index=True)
    po_line_id: Mapped[int] = mapped_column(ForeignKey("pur_order_lines.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inv_items.id"), index=True)
    qty_declared: Mapped[float] = mapped_column(Numeric(18, 4))                  # الكمية المعلن توريدها
    qty_accepted: Mapped[float] = mapped_column(Numeric(18, 4), default=0)       # المقبولة بعد الفحص (يحدّثها GRN)
    qty_rejected: Mapped[float] = mapped_column(Numeric(18, 4), default=0)       # المرفوضة لعيب صناعة/نقص

    note: Mapped[DeliveryNote] = relationship(back_populates="lines")


class GoodsReceipt(Base):
    """إذن الاستلام — يربط أمر الشراء بحركات المخزون الواردة والفاتورة."""
    __tablename__ = "pur_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("pur_orders.id"), index=True)
    dn_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ربط منطقي بإذن التوريد (اختياري)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("inv_warehouses.id"))
    date: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    invoice_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ربط منطقي بفاتورة الشراء
    supplier_invoice_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)  # رقم فاتورة المورد (للمطابقة الثلاثية)
    inspector: Mapped[str | None] = mapped_column(String(128), nullable=True)  # أمين المستودع/الفاحص
    status: Mapped[str] = mapped_column(String(16), default="posted")       # posted/cancelled
    remarks: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(64))

    order: Mapped[PurchaseOrder] = relationship()
    lines: Mapped[list["GoodsReceiptLine"]] = relationship(back_populates="receipt",
                                                           cascade="all, delete-orphan")


class GoodsReceiptLine(Base):
    __tablename__ = "pur_receipt_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("pur_receipts.id"), index=True)
    po_line_id: Mapped[int] = mapped_column(ForeignKey("pur_order_lines.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inv_items.id"), index=True)
    qty: Mapped[float] = mapped_column(Numeric(18, 4))                       # الكمية المقبولة فعليًا
    qty_rejected: Mapped[float] = mapped_column(Numeric(18, 4), default=0)   # المرفوضة عند الفحص النوعي
    unit_cost: Mapped[float] = mapped_column(Numeric(18, 4), default=0)

    receipt: Mapped[GoodsReceipt] = relationship(back_populates="lines")


class PurchaseInvoice(Base):
    __tablename__ = "pur_invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("pur_suppliers.id"), index=True)
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("inv_warehouses.id"), nullable=True)  # كان مفقودًا من الموديل
    po_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ربط منطقي بأمر الشراء داخل نفس القسم
    grn_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # إذن الاستلام المرتبط (السحب التلقائي للكميات)
    date: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    subtotal: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    discount: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    freight_charges: Mapped[float] = mapped_column(Numeric(18, 4), default=0)  # مصاريف شحن/نقل موزعة على التكلفة
    customs_charges: Mapped[float] = mapped_column(Numeric(18, 4), default=0)  # جمارك ومشتقات إضافية (IAS 2: تدخل في التكلفة)
    tax_rate: Mapped[float] = mapped_column(Numeric(6, 4), default=0)
    tax_amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    total: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    paid_amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    remarks: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(64))

    supplier: Mapped[Supplier] = relationship()
    lines: Mapped[list["PurchaseInvoiceLine"]] = relationship(back_populates="invoice",
                                                              cascade="all, delete-orphan")


class PurchaseInvoiceLine(Base):
    __tablename__ = "pur_invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("pur_invoices.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inv_items.id"), index=True)
    qty: Mapped[float] = mapped_column(Numeric(18, 4))
    unit_cost: Mapped[float] = mapped_column(Numeric(18, 4))
    line_total: Mapped[float] = mapped_column(Numeric(18, 4))

    invoice: Mapped[PurchaseInvoice] = relationship(back_populates="lines")
