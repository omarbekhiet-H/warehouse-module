# -*- coding: utf-8 -*-
"""قسم الموارد البشرية — مخططات Pydantic."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class EmployeeBase(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name_ar: str = Field(min_length=1, max_length=255)
    job_title: str | None = None
    phone: str | None = None
    email: str | None = None
    hire_date: dt.date | None = None
    base_salary: float = Field(ge=0, default=0)
    allowances: float = Field(ge=0, default=0)
    deductions: float = Field(ge=0, default=0)
    department: str | None = None
    salary_expense_account_id: int | None = None
    is_active: bool = True
    notes: str | None = None


class EmployeeCreate(EmployeeBase):
    pass


class EmployeeUpdate(BaseModel):
    name_ar: str | None = None
    job_title: str | None = None
    phone: str | None = None
    email: str | None = None
    base_salary: float | None = Field(ge=0, default=None)
    allowances: float | None = Field(ge=0, default=None)
    deductions: float | None = Field(ge=0, default=None)
    department: str | None = None
    salary_expense_account_id: int | None = None
    is_active: bool | None = None
    notes: str | None = None


class EmployeeOut(EmployeeBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class PayrollCreate(BaseModel):
    employee_id: int
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")  # YYYY-MM


class PayrollOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    voucher_no: str
    employee_id: int
    period: str
    gross: float
    deductions: float
    net: float
    paid: bool
    date: dt.datetime
    created_by: str | None
