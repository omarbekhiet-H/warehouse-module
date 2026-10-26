# -*- coding: utf-8 -*-
"""قسم الموارد البشرية — مخططات Pydantic."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PERIOD_RE = r"^\d{4}-(0[1-9]|1[0-2])$"


# ---------- الأقسام والفروع ----------
class DepartmentBase(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name_ar: str = Field(min_length=1, max_length=128)
    manager_name: str | None = None
    salary_expense_account_id: int | None = None
    is_active: bool = True


class DepartmentCreate(DepartmentBase):
    pass


class DepartmentOut(DepartmentBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class BranchBase(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name_ar: str = Field(min_length=1, max_length=128)
    address: str | None = None
    is_active: bool = True


class BranchCreate(BranchBase):
    pass


class BranchOut(BranchBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ---------- الموظفون ----------
class EmployeeBase(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    national_id: str | None = Field(default=None, max_length=14)
    name_ar: str = Field(min_length=1, max_length=255)
    job_title: str | None = None
    phone: str | None = None
    email: str | None = None
    hire_date: dt.date | None = None
    termination_date: dt.date | None = None
    department_id: int | None = None
    branch_id: int | None = None
    base_salary: float = Field(ge=0, default=0)
    allowances: float = Field(ge=0, default=0)
    insurance_rate: float = Field(ge=0, le=1, default=0.1)
    tax_rate: float = Field(ge=0, le=1, default=0.0)
    payment_method: Literal["cash", "bank_transfer", "check"] = "cash"
    bank_account_no: str | None = None
    salary_expense_account_id: int | None = None
    is_active: bool = True
    notes: str | None = None


class EmployeeCreate(EmployeeBase):
    pass


class EmployeeUpdate(BaseModel):
    name_ar: str | None = None
    national_id: str | None = None
    job_title: str | None = None
    phone: str | None = None
    email: str | None = None
    hire_date: dt.date | None = None
    termination_date: dt.date | None = None
    department_id: int | None = None
    branch_id: int | None = None
    base_salary: float | None = Field(ge=0, default=None)
    allowances: float | None = Field(ge=0, default=None)
    insurance_rate: float | None = Field(ge=0, le=1, default=None)
    tax_rate: float | None = Field(ge=0, le=1, default=None)
    payment_method: Literal["cash", "bank_transfer", "check"] | None = None
    bank_account_no: str | None = None
    salary_expense_account_id: int | None = None
    is_active: bool | None = None
    notes: str | None = None


class EmployeeOut(EmployeeBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    department_name: str | None = None
    branch_name: str | None = None


# ---------- الحضور ----------
class AttendanceIn(BaseModel):
    employee_id: int
    work_date: dt.date
    status: Literal["present", "absent", "leave", "holiday"] = "present"
    check_in: dt.time | None = None
    check_out: dt.time | None = None
    overtime_hours: float = Field(ge=0, le=24, default=0)
    notes: str | None = None


class AttendanceOut(AttendanceIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    employee_name: str | None = None


# ---------- السلف والقروض ----------
class LoanCreate(BaseModel):
    employee_id: int
    kind: Literal["advance", "loan"] = "advance"
    principal: float = Field(gt=0)
    installments_count: int = Field(ge=1, le=60, default=1)
    start_period: str = Field(pattern=PERIOD_RE)
    notes: str | None = None


class LoanAction(BaseModel):
    reason: str | None = None


class LoanDisburse(BaseModel):
    """صرف السلفة/القرض عبر الخزينة — طريقة السداد والحساب والصندوق."""
    pay_method: Literal["cash", "bank_transfer", "check"] = "cash"
    account_id: int                       # tre_accounts.id (صندوق/بنك)
    shift_id: int | None = None
    date: dt.date | None = None


class InstallmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    period: str
    seq: int
    amount: float
    status: str


class LoanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    loan_no: str
    employee_id: int
    employee_name: str | None = None
    kind: str
    principal: float
    installments_count: int
    installment_amount: float
    start_period: str
    disbursed_at: dt.date | None
    repaid_amount: float
    remaining: float
    status: str
    voucher_ref: str | None
    notes: str | None
    schedule: list[InstallmentOut] = []


# ---------- مسير الرواتب ----------
class PayrollRunCreate(BaseModel):
    period: str = Field(pattern=PERIOD_RE)
    branch_id: int | None = None
    include_inactive: bool = False        # لا يُنصح به إلا لتسويات نهاية الخدمة
    remarks: str | None = None


class PayrollDetailAdjust(BaseModel):
    """تعديل سطر المسير قبل الاعتماد (بدلات مناسباتية/جزاءات يدوية)."""
    allowances_extra: float = Field(ge=0, default=0)
    overtime_amount: float | None = Field(ge=0, default=None)
    partial_deduction: float | None = Field(ge=0, default=None)
    remarks: str | None = None


class PayrollPaymentIn(BaseModel):
    """بيانات صرف المسير عبر الخزينة عند الاعتماد النهائي."""
    pay_method: Literal["cash", "bank_transfer", "check"] = "cash"
    account_id: int
    shift_id: int | None = None
    date: dt.date | None = None


class PayrollDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    run_id: int
    employee_id: int
    employee_name: str | None = None
    department_name: str | None = None
    base_salary: float
    allowances: float
    overtime_hours: float
    overtime_amount: float
    absence_days: float
    absence_deduction: float
    gross: float
    insurance_deduction: float
    tax_deduction: float
    loan_deduction: float
    partial_deduction: float
    deductions_total: float
    net: float
    paid: bool
    payment_ref: str | None
    remarks: str | None


class PayrollRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    run_no: str
    period: str
    branch_id: int | None
    status: str
    employees_count: int
    gross_total: float
    deductions_total: float
    net_total: float
    accrual_entry_id: int | None
    payment_entry_id: int | None
    withholdings_entry_id: int | None
    payment_voucher_ref: str | None
    prepared_by: str | None
    approved_by: str | None
    remarks: str | None
    details: list[PayrollDetailOut] = []
