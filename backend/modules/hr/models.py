# -*- coding: utf-8 -*-
"""قسم الموارد البشرية وشئون العاملين — الجداول.

النطاق الوظيفي (وفق أفضل ممارسات ERP):
1) الهيكل التنظيمي: الأقسام (Departments) والفروع (Branches).
2) الملف الوظيفي للموظف (Employees): بيانات التعيين، الراتب الأساسي،
   البدلات الثابتة، نسبة التأمينات، رقم الحساب البنكي/الصرف.
3) الحضور والانصراف (Attendance): تسجيل اليوميات (حاضر/غائب/إجازة/أجر إضافي)
   لتغذية مسير الرواتب آليًا (ساعات الإضافي + أيام الغياب).
4) السلف والقروض (Loans): خصم شهري بالأقساط مع جدول استهلاك ومتابعة المتبقي،
   وربط صرف السند نقديًا عبر خدمات الخزينة.
5) مسير الرواتب الشهري (PayrollRun + PayrollDetail): حساب الاستحقاقات
   (أساسي + بدل + إضافي − غياب) والاستقطاعات (تأمينات + قروض + سلف + جزئي)،
   ثم الترحيل الآلي لقيود الأستاذ العام عند الاعتماد:
      - قيد الاعتراف بالمصروف: مدين مصروف الرواتب (حسب القسم) / دائن مستحقات الموظفين.
      - قيد الصرف: مدين مستحقات الموظفين / دائن الصندوق أو البنك.
      - قيد التزامات الاستقطاع: مدين مستحقات الموظفين / دائن هيئة التأمينات + مصلحة الضرائب.

لا يوجد FK عابر للأقسام؛ التكامل المالي يتم حصريًا عبر طبقة الخدمات (services→services).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class Department(Base):
    """قسم إداري/وظيفي (مالية، مخازن، مبيعات...) — يُستخدم للتجميع المحاسبي للمصروف."""
    __tablename__ = "hr_departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name_ar: Mapped[str] = mapped_column(String(128))
    manager_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # ربط منطقي بحساب أستاذ لمصروف رواتب هذا القسم (بدون FK عابر للأقسام)
    salary_expense_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class Branch(Base):
    """فرع الشركة — لربط الموظفين والحركات بالنطاق الجغرافي/المخزني."""
    __tablename__ = "hr_branches"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name_ar: Mapped[str] = mapped_column(String(128))
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class Employee(Base):
    """الملف الوظيفي للموظف — مرتبط منطقيًا بالقسم والفرع."""
    __tablename__ = "hr_employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    national_id: Mapped[str | None] = mapped_column(String(14), nullable=True, index=True)
    name_ar: Mapped[str] = mapped_column(String(255))
    job_title: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(128))
    hire_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    termination_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    department_id: Mapped[int | None] = mapped_column(ForeignKey("hr_departments.id"), nullable=True, index=True)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("hr_branches.id"), nullable=True, index=True)

    base_salary: Mapped[float] = mapped_column(Numeric(18, 4), default=0)     # الراتب الأساسي
    allowances: Mapped[float] = mapped_column(Numeric(18, 4), default=0)      # البدلات الشهرية الثابتة
    insurance_rate: Mapped[float] = mapped_column(Numeric(6, 3), default=0.1) # نسبة حصة الموظف التأمينات (على الأساسي)
    tax_rate: Mapped[float] = mapped_column(Numeric(6, 3), default=0.0)       # نسبة ضريبة الدخل المستقطعة (مبسطة)
    payment_method: Mapped[str] = mapped_column(String(16), default="cash")   # cash/bank_transfer/check
    bank_account_no: Mapped[str | None] = mapped_column(String(34), nullable=True)

    # ربط منطقي بدليل الحسابات (للتجميع التفصيلي إن لزم)
    salary_expense_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    department: Mapped[Department | None] = relationship()
    branch: Mapped[Branch | None] = relationship()


class Attendance(Base):
    """يوميات الحضور والانصراف — تغذي المسير آليًا (ساعات إضافية / خصم غياب)."""
    __tablename__ = "hr_attendance"
    __table_args__ = (UniqueConstraint("employee_id", "work_date", name="uq_hr_emp_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("hr_employees.id"), index=True)
    work_date: Mapped[dt.date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(16), default="present")  # present/absent/leave/holiday
    check_in: Mapped[dt.time | None] = mapped_column(Time, nullable=True)
    check_out: Mapped[dt.time | None] = mapped_column(Time, nullable=True)
    overtime_hours: Mapped[float] = mapped_column(Numeric(6, 2), default=0)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    employee: Mapped[Employee] = relationship()


class Loan(Base):
    """سلفة/قرض موظف — يُسدد بأقساط شهرية تُستقطع من المسير (IAS 19 مبسط للسلع المقدمة للعاملين)."""
    __tablename__ = "hr_loans"

    id: Mapped[int] = mapped_column(primary_key=True)
    loan_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("hr_employees.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="advance")  # advance سلفة / loan قرض
    principal: Mapped[float] = mapped_column(Numeric(18, 4))          # أصل المبلغ
    installments_count: Mapped[int] = mapped_column(Integer, default=1)
    installment_amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    start_period: Mapped[str] = mapped_column(String(7))              # YYYY-MM أول قسط
    disbursed_at: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    repaid_amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    remaining: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/approved/paying/settled/rejected
    voucher_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)  # رقم سند الصرف المرتبط (من الخزينة)
    journal_entry_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    employee: Mapped[Employee] = relationship()
    schedule: Mapped[list["LoanInstallment"]] = relationship(
        back_populates="loan", cascade="all, delete-orphan")


class LoanInstallment(Base):
    """جدول استهلاك أقساط السلفة/القرض — يربط كل قسط بشهر المسير الذي استُقطع فيه."""
    __tablename__ = "hr_loan_installments"
    __table_args__ = (UniqueConstraint("loan_id", "period", name="uq_hr_loan_period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    loan_id: Mapped[int] = mapped_column(ForeignKey("hr_loans.id"), index=True)
    period: Mapped[str] = mapped_column(String(7))          # YYYY-MM
    seq: Mapped[int] = mapped_column(Integer, default=1)
    amount: Mapped[float] = mapped_column(Numeric(18, 4))
    deducted_detail_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # سطر المسير الذي استقطع القسط
    status: Mapped[str] = mapped_column(String(16), default="due")  # due/deducted/waived
    deducted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    loan: Mapped[Loan] = relationship(back_populates="schedule")


class PayrollRun(Base):
    """مسير رواتب شهري — رأس الدورة المستندية للرواتب (draft → posted → paid)."""
    __tablename__ = "hr_payroll_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)   # YYYY-MM
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("hr_branches.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/posted/paid/cancelled
    employees_count: Mapped[int] = mapped_column(Integer, default=0)
    gross_total: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    deductions_total: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    net_total: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    accrual_entry_id: Mapped[int | None] = mapped_column(Integer, nullable=True)   # قيد الاعتراف بالمصروف
    payment_entry_id: Mapped[int | None] = mapped_column(Integer, nullable=True)   # قيد الصرف من النقدية
    withholdings_entry_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # قيد التزامات الاستقطاع
    payment_voucher_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prepared_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    details: Mapped[list["PayrollDetail"]] = relationship(
        back_populates="run", cascade="all, delete-orphan")


class PayrollDetail(Base):
    """سطر المسير لكل موظف — تفصيل الاستحقاقات والاستقطاعات وقابل للتدقيق."""
    __tablename__ = "hr_payroll_details"
    __table_args__ = (UniqueConstraint("run_id", "employee_id", name="uq_hr_run_emp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("hr_payroll_runs.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("hr_employees.id"), index=True)

    base_salary: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    allowances: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    overtime_hours: Mapped[float] = mapped_column(Numeric(6, 2), default=0)
    overtime_amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    absence_days: Mapped[float] = mapped_column(Numeric(6, 2), default=0)
    absence_deduction: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    gross: Mapped[float] = mapped_column(Numeric(18, 4), default=0)

    insurance_deduction: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    tax_deduction: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    loan_deduction: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    partial_deduction: Mapped[float] = mapped_column(Numeric(18, 4), default=0)  # استقطاع يدوي/جزاءات
    deductions_total: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    net: Mapped[float] = mapped_column(Numeric(18, 4), default=0)

    paid: Mapped[bool] = mapped_column(default=False)
    payment_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(255), nullable=True)

    run: Mapped[PayrollRun] = relationship(back_populates="details")
    employee: Mapped[Employee] = relationship()
