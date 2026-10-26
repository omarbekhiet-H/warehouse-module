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


class PurchaseInvoice(Base):
    __tablename__ = "pur_invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("pur_suppliers.id"), index=True)
    date: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    subtotal: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    discount: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
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
