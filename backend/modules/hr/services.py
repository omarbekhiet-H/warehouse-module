# -*- coding: utf-8 -*-
"""قسم الموارد البشرية — منطق الأعمال + التكامل المحاسبي.

قاعدة الربط بين الأقسام: الاستيراد من طبقة خدمات المحاسبة فقط،
ولا يتم الوصول لجداول قسم آخر مباشرة.
"""
from __future__ import annotations

import datetime as dt

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.modules.accounting import schemas as acc_schemas
from backend.modules.accounting import services as acc_services
from backend.modules.hr import models, schemas


# ---------- الموظفون ----------
def list_employees(db: Session, search: str | None = None):
    stmt = select(models.Employee).where(models.Employee.is_active.is_(True))
    if search:
        like = f"%{search}%"
        stmt = stmt.where(models.Employee.name_ar.like(like) | models.Employee.code.like(like))
    return list(db.scalars(stmt.order_by(models.Employee.name_ar)))


def create_employee(db: Session, data: schemas.EmployeeCreate) -> models.Employee:
    if db.scalar(select(models.Employee).where(models.Employee.code == data.code)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"كود الموظف {data.code} مستخدم بالفعل")
    obj = models.Employee(**data.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def update_employee(db: Session, employee_id: int, data: schemas.EmployeeUpdate) -> models.Employee:
    obj = db.get(models.Employee, employee_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الموظف غير موجود")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return obj


# ---------- الرواتب ----------
def run_payroll(db: Session, data: schemas.PayrollCreate, user: dict) -> models.PayrollRun:
    emp = db.get(models.Employee, data.employee_id)
    if not emp:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "الموظف غير موجود")
    dup = db.scalar(select(models.PayrollRun).where(
        models.PayrollRun.employee_id == emp.id, models.PayrollRun.period == data.period))
    if dup:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"تم تصفية راتب {data.period} سابقًا للقسيمة {dup.voucher_no}")

    gross = float(emp.base_salary) + float(emp.allowances)
    ded = float(emp.deductions)
    net = max(gross - ded, 0)

    seq = (db.scalar(select(func.count(models.PayrollRun.id))) or 0) + 1
    run = models.PayrollRun(
        voucher_no=f"PR-{data.period.replace('-', '')}-{seq:05d}",
        employee_id=emp.id, period=data.period,
        gross=gross, deductions=ded, net=net,
        created_by=user.get("user_id"),
    )
    db.add(run)
    db.flush()

    # القيد المحاسبي: مدين مصروف الرواتب، دائن الصندوق/البنك (إن كان معرّفًا)
    acct_id = emp.salary_expense_account_id or _default_salary_account(db)
    cash_acct = _first_account_by_code(db, "1100")
    if acct_id and cash_acct and net > 0:
        acc_services.create_journal_entry(
            db,
            acc_schemas.JournalEntryCreate(
                date=dt.date.today(),
                description=f"رواتب {data.period} — {emp.name_ar}",
                lines=[
                    acc_schemas.JournalLineIn(account_id=acct_id, debit=net, credit=0),
                    acc_schemas.JournalLineIn(account_id=cash_acct, debit=0, credit=net),
                ],
            ),
            user, source_module="hr", source_ref=run.voucher_no,
        )
    db.commit()
    db.refresh(run)
    return run


def payroll_report(db: Session, period: str | None = None):
    stmt = select(models.PayrollRun)
    if period:
        stmt = stmt.where(models.PayrollRun.period == period)
    runs = list(db.scalars(stmt.order_by(models.PayrollRun.id.desc())))
    total_net = sum(float(r.net) for r in runs)
    return {"rows": [schemas.PayrollOut.model_validate(r) for r in runs],
            "count": len(runs), "total_net": round(total_net, 4)}


# ---------- مساعدات داخلية ----------
def _first_account_by_code(db: Session, code: str) -> int | None:
    from backend.modules.accounting import models as acc_models
    acct = db.scalar(select(acc_models.Account).where(acc_models.Account.code == code))
    return acct.id if acct else None


def _default_salary_account(db: Session) -> int | None:
    from backend.modules.accounting import models as acc_models
    acct = db.scalar(select(acc_models.Account).where(acc_models.Account.code == "5100")) \
        or db.scalar(select(acc_models.Account).where(
            acc_models.Account.account_type == "expense").limit(1))
    return acct.id if acct else None
