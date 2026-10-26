# -*- coding: utf-8 -*-
"""قسم المبيعات — الجداول (عملاء / فواتير).

التكامل: فاتورة البيع ⇒ حركة مخزون صادرة (عبر services المخازن)
⇒ قيد محاسبي تلقائي (عبر services المحاسبة). الروابط كلها عبر طبقات الخدمات.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class Customer(Base):
    __tablename__ = "sal_customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name_ar: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))          # كان ناقصًا سابقًا
    email: Mapped[str | None] = mapped_column(String(128))         # كان ناقصًا سابقًا
    address: Mapped[str | None] = mapped_column(Text)              # كان ناقصًا سابقًا
    tax_number: Mapped[str | None] = mapped_column(String(32))     # كان ناقصًا سابقًا
    receivable_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ربط منطقي بالمحاسبة (لا FK عابر للأقسام)
    is_active: Mapped[bool] = mapped_column(default=True)


class Invoice(Base):
    __tablename__ = "sal_invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("sal_customers.id"), index=True)
    date: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    subtotal: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    discount: Mapped[float] = mapped_column(Numeric(18, 4), default=0)   # كان ناقصًا سابقًا
    tax_rate: Mapped[float] = mapped_column(Numeric(6, 4), default=0)    # كان ناقصًا سابقًا
    tax_amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    total: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    paid_amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0)  # كان ناقصًا سابقًا
    status: Mapped[str] = mapped_column(String(16), default="draft")       # draft/posted/paid/cancelled
    remarks: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(64))

    customer: Mapped[Customer] = relationship()
    lines: Mapped[list["InvoiceLine"]] = relationship(back_populates="invoice",
                                                      cascade="all, delete-orphan")


class InvoiceLine(Base):
    __tablename__ = "sal_invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("sal_invoices.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inv_items.id"), index=True)
    qty: Mapped[float] = mapped_column(Numeric(18, 4))
    unit_price: Mapped[float] = mapped_column(Numeric(18, 4))
    line_total: Mapped[float] = mapped_column(Numeric(18, 4))

    invoice: Mapped[Invoice] = relationship(back_populates="lines")
