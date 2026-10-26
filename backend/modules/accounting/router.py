# -*- coding: utf-8 -*-
"""قسم المحاسبة — نقاط النهاية (Router مستقل بالكامل).

المسار الأساسي: /api/v1/accounting/...
لا يستورد هذا الملف أي شيء من الأقسام الأخرى؛ التكامل مع المخازن/المبيعات
يتم عبر استدعاء services الخاص بالمحاسبة من داخل تلك الأقسام عند الحاجة
أو عبر webhook داخلي، مع توثيق رقم المستند المصدر (source_ref).
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.config import get_current_user, module_deps
from backend.core.database import get_db
from backend.core.schemas import ApiResponse, PageParams
from backend.modules.accounting import models, schemas, services

router = APIRouter(
    prefix="/accounting",
    tags=["المحاسبة"],
    dependencies=[Depends(get_current_user)],
)
health = router.get("/health")(module_deps("accounting"))


# ---------- دليل الحسابات ----------
@router.get("/accounts", response_model=ApiResponse[list[schemas.AccountOut]], summary="قائمة الحسابات")
def list_accounts(search: str | None = None, db: Session = Depends(get_db)):
    return ApiResponse.ok(services.list_accounts(db, PageParams(search=search)))


@router.post("/accounts", response_model=ApiResponse[schemas.AccountOut], summary="إضافة حساب")
def create_account(data: schemas.AccountCreate, db: Session = Depends(get_db)):
    return ApiResponse.ok(schemas.AccountOut.model_validate(services.create_account(db, data)),
                          message="تم إنشاء الحساب بنجاح")


@router.put("/accounts/{account_id}", response_model=ApiResponse[schemas.AccountOut], summary="تعديل حساب")
def update_account(account_id: int, data: schemas.AccountUpdate, db: Session = Depends(get_db)):
    return ApiResponse.ok(schemas.AccountOut.model_validate(services.update_account(db, account_id, data)))


@router.delete("/accounts/{account_id}", response_model=ApiResponse, summary="حذف حساب")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    services.delete_account(db, account_id)
    return ApiResponse.ok(message="تم حذف الحساب")


# ---------- القيود ----------
@router.post("/journal-entries", response_model=ApiResponse[schemas.JournalEntryOut], summary="تسجيل قيد يومية")
def create_entry(data: schemas.JournalEntryCreate,
                 db: Session = Depends(get_db),
                 user: dict = Depends(get_current_user)):
    entry = services.create_journal_entry(db, data, user)
    return ApiResponse.ok(schemas.JournalEntryOut.model_validate(entry), message="تم حفظ القيد")


@router.get("/journal-entries", response_model=ApiResponse[list[schemas.JournalEntryOut]], summary="استعلام القيود")
def list_entries(source_ref: str | None = Query(None, description="ربط القيد بمستند من قسم آخر"),
                 db: Session = Depends(get_db)):
    stmt = db.query(models.JournalEntry).order_by(models.JournalEntry.id.desc())
    if source_ref:
        stmt = stmt.filter(models.JournalEntry.source_ref == source_ref)
    return ApiResponse.ok([schemas.JournalEntryOut.model_validate(e) for e in stmt.all()])


# ---------- التقارير ----------
@router.get("/reports/trial-balance", response_model=ApiResponse, summary="ميزان المراجعة")
def trial_balance(date_from: dt.date | None = None, date_to: dt.date | None = None,
                  db: Session = Depends(get_db)):
    rows = services.trial_balance(db, date_from, date_to)
    totals = {
        "total_debit": round(sum(r["debit"] for r in rows), 4),
        "total_credit": round(sum(r["credit"] for r in rows), 4),
    }
    return ApiResponse.ok({"rows": rows, "totals": totals})
