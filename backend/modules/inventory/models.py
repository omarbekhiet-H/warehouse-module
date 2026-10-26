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
    purchase_price: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    sale_price: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    reorder_level: Mapped[float] = mapped_column(Numeric(18, 4), default=0)  # حد الطلب
    tax_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ربط محاسبي (منطقي، لا FK عابر للأقسام)
    inventory_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)


class StockMovement(Base):
    __tablename__ = "inv_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inv_items.id"), index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("inv_warehouses.id"), index=True)
    movement_type: Mapped[str] = mapped_column(String(16))  # in/out/transfer/adjust
    qty: Mapped[float] = mapped_column(Numeric(18, 4))
    unit_cost: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    date: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64))

    item: Mapped[Item] = relationship()
    warehouse: Mapped[Warehouse] = relationship()
