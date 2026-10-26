# -*- coding: utf-8 -*-
"""قسم المحاسبة — الجداول (دليل الحسابات + القيود)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class Account(Base):
    __tablename__ = "acc_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name_ar: Mapped[str] = mapped_column(String(128))
    name_en: Mapped[str | None] = mapped_column(String(128))
    account_type: Mapped[str] = mapped_column(String(32))  # asset/liability/equity/revenue/expense
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("acc_accounts.id"))
    is_active: Mapped[bool] = mapped_column(default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    journal_lines: Mapped[list["JournalLine"]] = relationship(back_populates="account")


class JournalEntry(Base):
    __tablename__ = "acc_journal_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    date: Mapped[dt.date] = mapped_column(DateTime)
    description: Mapped[str | None] = mapped_column(Text)
    source_module: Mapped[str | None] = mapped_column(String(32))  # inventory/sales/purchasing
    source_ref: Mapped[str | None] = mapped_column(String(64))      # رقم المستند في القسم الآخر
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    lines: Mapped[list["JournalLine"]] = relationship(back_populates="entry", cascade="all, delete-orphan")


class JournalLine(Base):
    __tablename__ = "acc_journal_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("acc_journal_entries.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("acc_accounts.id"), index=True)
    debit: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    credit: Mapped[float] = mapped_column(Numeric(18, 4), default=0)

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship(back_populates="journal_lines")
