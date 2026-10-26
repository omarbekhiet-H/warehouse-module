# -*- coding: utf-8 -*-
"""قسم المخازن — الجداول (أصناف / مخازن / حركة المخزون).

التكامل مع المحاسبة: كل حركة مخزونية تُغذّي القيد المحاسبي عبر
services.create_journal_entry مع تسجيل source_ref = رقم الحركة.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class Warehouse(Base):
    __tablename__ = "inv_warehouses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name_ar: Mapped[str] = mapped_column(String(128))
    location: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # حقل كان ناقصًا سابقًا


class Item(Base):
    __tablename__ = "inv_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    barcode: Mapped[str | None] = mapped_column(String(64), index=True)  # كان ناقصًا سابقًا
    name_ar: Mapped[str] = mapped_column(String(255))
    unit: Mapped[str] = mapped_column(String(32), default="حبة")         # كان ناقصًا سابقًا
    category: Mapped[str | None] = mapped_column(String(128))            # كان ناقصًا سابقًا
    purchase_price: Mapped[float] = mapped_column(Numeric(18, 4), default=0)   # سعر الشراء المرجعي (للتقارير فقط)
    sale_price: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    reorder_level: Mapped[float] = mapped_column(Numeric(18, 4), default=0)  # حد الطلب
    # ---- التكلفة المعيارية (Standard Costing — معتمد IAS 2 / GAAP) ----
    standard_cost: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    costing_method: Mapped[str] = mapped_column(String(8), default="std")     # std / avg / fifo
    avg_cost: Mapped[float] = mapped_column(Numeric(18, 4), default=0)        # المتوسط المتحرك المرجح (يُحدَّث تلقائيًا عند الوارد)
    on_hand_qty: Mapped[float] = mapped_column(Numeric(18, 4), default=0)     # الرصيد الكمي (مخزن افتراضي — يظل متزامنًا مع الحركات)
    # ---- الربط المحاسبي لكل صنف (Perpetual Inventory System) ----
    tax_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)      # حساب الضريبة على المبيعات (Output VAT)
    inventory_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # حساب المخزون (أصل)
    cogs_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)       # حساب تكلفة البضاعة المباعة (مصروف)
    expense_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)    # حساب المصروف لصرفات غير البيع (مستهلكات/إداري)
    is_active: Mapped[bool] = mapped_column(default=True)


class StockMovement(Base):
    __tablename__ = "inv_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inv_items.id"), index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("inv_warehouses.id"), index=True)
    dest_warehouse_id: Mapped[int | None] = mapped_column(
        ForeignKey("inv_warehouses.id"), nullable=True)          # وجهه التحويل (كانت ناقصة)
    movement_type: Mapped[str] = mapped_column(String(16))  # in/out/transfer/adjust
    qty: Mapped[float] = mapped_column(Numeric(18, 4))
    unit_cost: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    cost_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # std/avg/purchase/manual — مصدر التكلفة المطبقة
    valuation_value: Mapped[float] = mapped_column(Numeric(18, 4), default=0)   # قيمة التقييم = qty × unit_cost (محفوظة للمراجعة)
    source_doc: Mapped[str | None] = mapped_column(String(64), index=True)  # ربط بالمستند الأصلي (MR/PO/RECEIPT/SO)
    date: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64))
    is_posted: Mapped[bool] = mapped_column(default=True)   # حركة مرحّلة محاسبيًا؟ (False في تسويات الجرد اليدوية الاختيارية)

    item: Mapped[Item] = relationship()
    warehouse: Mapped[Warehouse] = relationship(foreign_keys=[warehouse_id])
    dest_warehouse: Mapped[Warehouse | None] = relationship(foreign_keys=[dest_warehouse_id])


class StockCount(Base):
    """جرد المخزون — رأس الجرد بخطوطه، والتسويات تُنتج حركات adjust مرتبطة به."""
    __tablename__ = "inv_stock_counts"

    id: Mapped[int] = mapped_column(primary_key=True)
    count_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("inv_warehouses.id"), index=True)
    date: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/posted/cancelled
    auto_post_adjustments: Mapped[bool] = mapped_column(default=True)  # ترحيل التسويات المحاسبية تلقائيًا عند الاعتماد؟
    variance_value: Mapped[float] = mapped_column(Numeric(18, 4), default=0)  # صافي قيمة الفروق (موجب = ربح جرد / سالب = خسارة)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64))
    posted_by: Mapped[str | None] = mapped_column(String(64))   # مبدأ الفصل بين المهام: مُنشئ ≠ معتمِد
    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    warehouse: Mapped[Warehouse] = relationship()
    lines: Mapped[list["StockCountLine"]] = relationship(back_populates="count",
                                                         cascade="all, delete-orphan")


class StockCountLine(Base):
    __tablename__ = "inv_stock_count_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    count_id: Mapped[int] = mapped_column(ForeignKey("inv_stock_counts.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inv_items.id"), index=True)
    system_qty: Mapped[float] = mapped_column(Numeric(18, 4), default=0)   # رصيد النظام لحظة الجرد
    counted_qty: Mapped[float] = mapped_column(Numeric(18, 4))             # الرصيد الفعلي المعدود
    variance_qty: Mapped[float] = mapped_column(Numeric(18, 4), default=0)  # الفرق = معدود - نظام
    unit_cost: Mapped[float] = mapped_column(Numeric(18, 4), default=0)     # تكلفة الوحدة لحظة الجرد (للتسوية)
    adjustment_moved: Mapped[bool] = mapped_column(default=False)  # هل صدرت حركة التسوية؟

    count: Mapped[StockCount] = relationship(back_populates="lines")
    item: Mapped[Item] = relationship()
