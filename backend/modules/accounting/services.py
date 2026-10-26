# -*- coding: utf-8 -*-
"""قسم المحاسبة — منطق الأعمال."""
from __future__ import annotations

import datetime as dt

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.modules.accounting import models, schemas


def list_accounts(db: Session, params: schemas.PageParams | None = None):
    stmt = select(models.Account).order_by(models.Account.code)
    if params and params.search:
        like = f"%{params.search}%"
        stmt = stmt.where(models.Account.name_ar.like(like) | models.Account.code.like(like))
    return list(db.scalars(stmt))


def create_account(db: Session, data: schemas.AccountCreate) -> models.Account:
    exists = db.scalar(select(models.Account).where(models.Account.code == data.code))
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, f"كود الحساب {data.code} مستخدم بالفعل")
    obj = models.Account(**data.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def update_account(db: Session, account_id: int, data: schemas.AccountUpdate) -> models.Account:
    obj = db.get(models.Account, account_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الحساب غير موجود")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return obj


def delete_account(db: Session, account_id: int) -> None:
    obj = db.get(models.Account, account_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الحساب غير موجود")
    if obj.journal_lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "لا يمكن حذف حساب عليه قيود")
    db.delete(obj)
    db.commit()


def create_journal_entry(db: Session, data: schemas.JournalEntryCreate,
                         user: dict, source_module: str | None = None,
                         source_ref: str | None = None) -> models.JournalEntry:
    total_debit = sum(l.debit for l in data.lines)
    total_credit = sum(l.credit for l in data.lines)
    if abs(total_debit - total_credit) > 0.0001:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"القيد غير متوازن: مدين {total_debit} <> دائن {total_credit}")
    seq = (db.scalar(select(func.count(models.JournalEntry.id))) or 0) + 1
    entry_no = f"JE-{dt.date.today():%Y%m%d}-{seq:05d}"

    # التحقق من وجود كل الحسابات أولًا قبل إنشاء أي كائن،
    # حتى لا يبقى قيد ناقص في الجلسة عند رفع خطأ.
    lines_ = []
    for line in data.lines:
        acct = db.get(models.Account, line.account_id)
        if not acct:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"الحساب رقم {line.account_id} غير موجود في دليل الحسابات")
        lines_.append(models.JournalLine(
            account_id=line.account_id, debit=line.debit, credit=line.credit))

    entry = models.JournalEntry(
        entry_no=entry_no,
        date=dt.datetime.combine(data.date, dt.time()),
        description=data.description,
        source_module=source_module,
        source_ref=source_ref,
        created_by=user.get("user_id"),
        lines=lines_,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def trial_balance(db: Session, date_from: dt.date | None = None,
                  date_to: dt.date | None = None):
    stmt = (
        select(
            models.Account.id,
            models.Account.code,
            models.Account.name_ar,
            func.coalesce(func.sum(models.JournalLine.debit), 0).label("debit"),
            func.coalesce(func.sum(models.JournalLine.credit), 0).label("credit"),
        )
        .join(models.JournalLine, models.JournalLine.account_id == models.Account.id, isouter=True)
        .join(models.JournalEntry, models.JournalEntry.id == models.JournalLine.entry_id, isouter=True)
        .group_by(models.Account.id)
        .order_by(models.Account.code)
    )
    if date_from:
        stmt = stmt.where(func.date(models.JournalEntry.date) >= dt.datetime.combine(date_from, dt.time()))
    if date_to:
        stmt = stmt.where(func.date(models.JournalEntry.date) <= dt.datetime.combine(date_to, dt.time()))
    rows = db.execute(stmt).all()
    return [
        {
            "account_id": r.id, "code": r.code, "name_ar": r.name_ar,
            "debit": float(r.debit), "credit": float(r.credit),
            "balance": float(r.debit) - float(r.credit),
        }
        for r in rows
    ]
