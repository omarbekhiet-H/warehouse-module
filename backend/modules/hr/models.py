# -*- coding: utf-8 -*-
"""قسم الموارد البشرية — الجداول (موظفون / رواتب).

التكامل مع المحاسبة يتم عبر طبقة الخدمات فقط (acc_services) مثل بقية الأقسام،
مع تسجيل source_ref = رقم قسيمة الرواتب.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class Employee(Base):
    __tablename__ = "hr_employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name_ar: Mapped[str] = mapped_column(String(255))
    job_title: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(128))
    hire_date: Mapped[dt.date | None] = mapped_column(DateTime, nullable=True)
    base_salary: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    allowances: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    deductions: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    department: Mapped[str | None] = mapped_column(String(128))
    # ربط منطقي بدليل الحسابات (بدون FK عابر للأقسام)
    salary_expense_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class PayrollRun(Base):
    __tablename__ = "hr_payrolls"

    id: Mapped[int] = mapped_column(primary_key=True)
    voucher_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("hr_employees.id"), index=True)
    period: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    gross: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    deductions: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    net: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    paid: Mapped[bool] = mapped_column(default=False)
    date: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    created_by: Mapped[str | None] = mapped_column(String(64))

    employee: Mapped[Employee] = relationship()
