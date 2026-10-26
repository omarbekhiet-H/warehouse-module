# -*- coding: utf-8 -*-
"""قسم الموارد البشرية — منطق الأعمال + التكامل المحاسبي والمخزني/الخزائني.

قواعد الربط بين الأقسام (معمارية المشروع):
    hr.services ──> accounting.services  : إنشاء القيود والأرصدة فقط عبر طبقة الخدمات.
    hr.services ──> treasury.services    : صرف السلف والمسائل النقدية تمر بسندات الخزينة
                                           لتُرحَّل لحسابات الصندوق/البنك في الأستاذ.
ولا يتم الوصول لجداول قسم آخر مباشرة؛ الروابط المنطقية تُخزن كأرقام/معرفات.

خريطة حسابات الموارد البشرية القياسية (تُنشأ تلقائيًا إن لم تكن موجودة):
    5200 مصروف رواتب وأجور                     expense
    5210 مصروف تأمينات اجتماعية (حصة صاحب العمل) expense
    2400 مستحقات الموظفين (رواتب تحت الصرف)     liability
    2410 سلف و قروض الموظفين (أصول/خصم حسب السياق — هنا أصل مقابل دائن عند الاستقطاع) asset
    2420 هيئة التأمينات الاجتماعية مستحق الدفع   liability
    2430 مصلحة الضرائب على الدخل مستحق الدفع     liability
    1100/1110 نقدية/بنك (من دليل الخزينة)
"""
from __future__ import annotations

import calendar
import datetime as dt

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.modules.accounting import schemas as acc_schemas
from backend.modules.accounting import services as acc_services
from backend.modules.hr import models, schemas

WORKDAYS_PER_MONTH = 26          # أساس احتساب أجر الساعة الإضافية واليوم المفقود
OVERTIME_RATE_FACTOR = 1.25      # 125% من الأجر الأساسي للساعة


# ==================================================================
# مساعدات الحسابات والدليل المحاسبي
# ==================================================================
def _acct(db: Session, code: str, name_ar: str, typ: str) -> int:
    """يضمن وجود حساب بدليل الأستاذ ويعيد معرفه (عبر خدمات المحاسبة)."""
    from backend.modules.accounting import models as acc_models
    acct = db.scalar(select(acc_models.Account).where(acc_models.Account.code == code))
    if not acct:
        acct = acc_services.create_account(
            db, acc_schemas.AccountCreate(code=code, name_ar=name_ar, account_type=typ))
    return acct.id


def salary_expense_gl(db: Session) -> int:
    return _acct(db, "5200", "مصروف رواتب وأجور", "expense")


def employer_insurance_gl(db: Session) -> int:
    return _acct(db, "5210", "مصروف تأمينات اجتماعية (حصة المنشأة)", "expense")


def payroll_payable_gl(db: Session) -> int:
    return _acct(db, "2400", "مستحقات الموظفين (رواتب تحت الصرف)", "liability")


def employee_advances_gl(db: Session) -> int:
    return _acct(db, "2410", "سلف وقروض الموظفين (مدينون)", "asset")


def insurance_payable_gl(db: Session) -> int:
    return _acct(db, "2420", "هيئة التأمينات الاجتماعية — مستحق دفع", "liability")


def tax_payable_gl(db: Session) -> int:
    return _acct(db, "2430", "مصلحة الضرائب على الدخل — مستحق دفع", "liability")


def cash_gl(db: Session) -> int:
    return _acct(db, "1100", "النقدية بالصندوق (Cash on Hand)", "asset")


def bank_gl(db: Session) -> int:
    return _acct(db, "1110", "البنك (حساب جاري)", "asset")


def _journal(db: Session, lines: list[tuple[int, float, float]], desc: str,
             user: dict | None, source_ref: str | None, date: dt.date | None = None):
    """بوابة ترحيل موحّدة لقيود الموارد البشرية إلى الأستاذ العام."""
    payload = acc_schemas.JournalEntryCreate(
        date=date or dt.date.today(),
        description=desc,
        lines=[acc_schemas.JournalLineIn(account_id=a, debit=d, credit=c)
               for a, d, c in lines],
    )
    return acc_services.create_journal_entry(
        db, payload, user or {"user_id": "system"},
        source_module="hr", source_ref=source_ref)


def _next_no(db: Session, model, prefix: str) -> str:
    seq = (db.scalar(select(func.count(model.id))) or 0) + 1
    return f"{prefix}-{dt.date.today():%Y%m%d}-{seq:05d}"


def _period_end(period: str) -> dt.date:
    y, m = map(int, period.split("-"))
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def _next_period(period: str) -> str:
    y, m = map(int, period.split("-"))
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return f"{y:04d}-{m:02d}"


# ==================================================================
# الهيكل التنظيمي: أقسام وفروع
# ==================================================================
def list_departments(db: Session):
    return list(db.scalars(select(models.Department).order_by(models.Department.name_ar)))


def create_department(db: Session, data: schemas.DepartmentCreate) -> models.Department:
    if db.scalar(select(models.Department).where(models.Department.code == data.code)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"كود القسم {data.code} مستخدم")
    obj = models.Department(**data.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


def list_branches(db: Session):
    return list(db.scalars(select(models.Branch).order_by(models.Branch.name_ar)))


def create_branch(db: Session, data: schemas.BranchCreate) -> models.Branch:
    if db.scalar(select(models.Branch).where(models.Branch.code == data.code)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"كود الفرع {data.code} مستخدم")
    obj = models.Branch(**data.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


# ==================================================================
# الموظفون
# ==================================================================
def list_employees(db: Session, search: str | None = None,
                   department_id: int | None = None, branch_id: int | None = None,
                   include_inactive: bool = False):
    stmt = select(models.Employee)
    if not include_inactive:
        stmt = stmt.where(models.Employee.is_active.is_(True))
    if department_id:
        stmt = stmt.where(models.Employee.department_id == department_id)
    if branch_id:
        stmt = stmt.where(models.Employee.branch_id == branch_id)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(models.Employee.name_ar.like(like) | models.Employee.code.like(like))
    return list(db.scalars(stmt.order_by(models.Employee.name_ar)))


def get_employee(db: Session, employee_id: int) -> models.Employee:
    emp = db.get(models.Employee, employee_id)
    if not emp:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الموظف غير موجود")
    return emp


def create_employee(db: Session, data: schemas.EmployeeCreate) -> models.Employee:
    if db.scalar(select(models.Employee).where(models.Employee.code == data.code)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"كود الموظف {data.code} مستخدم بالفعل")
    if data.national_id and db.scalar(
            select(models.Employee).where(models.Employee.national_id == data.national_id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "رقم البطاقة مسجل لموظف آخر")
    if data.department_id and not db.get(models.Department, data.department_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "القسم المحدد غير موجود")
    if data.branch_id and not db.get(models.Branch, data.branch_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "الفرع المحدد غير موجود")
    obj = models.Employee(**data.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


def update_employee(db: Session, employee_id: int, data: schemas.EmployeeUpdate) -> models.Employee:
    obj = get_employee(db, employee_id)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    db.commit(); db.refresh(obj)
    return obj


def employee_out(db: Session, e: models.Employee) -> schemas.EmployeeOut:
    dep = db.get(models.Department, e.department_id) if e.department_id else None
    br = db.get(models.Branch, e.branch_id) if e.branch_id else None
    d = schemas.EmployeeOut.model_validate(e, from_attributes=True)
    d.department_name = dep.name_ar if dep else None
    d.branch_name = br.name_ar if br else None
    return d


# ==================================================================
# الحضور والانصراف
# ==================================================================
def upsert_attendance(db: Session, data: schemas.AttendanceIn, user: dict) -> models.Attendance:
    emp = get_employee(db, data.employee_id)
    if not emp.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "لا يمكن تسجيل حضور لموظف موقوف")
    rec = db.scalar(select(models.Attendance).where(
        models.Attendance.employee_id == emp.id,
        models.Attendance.work_date == data.work_date))
    if rec:
        for k, v in data.model_dump(exclude={"employee_id", "work_date"}).items():
            setattr(rec, k, v)
    else:
        rec = models.Attendance(**data.model_dump(), created_by=user.get("user_id"))
        db.add(rec)
    db.commit(); db.refresh(rec)
    return rec


def attendance_summary(db: Session, employee_id: int, period: str) -> dict:
    """ملخص الشهر للموظف: أيام الغياب وساعات الإضافي — يغذي المسير آليًا."""
    start = f"{period}-01"
    end = _period_end(period).isoformat()
    rows = db.execute(
        select(models.Attendance.status, func.sum(models.Attendance.overtime_hours), func.count(models.Attendance.id))
        .where(models.Attendance.employee_id == employee_id,
               func.date(models.Attendance.work_date) >= start,
               func.date(models.Attendance.work_date) <= end)
        .group_by(models.Attendance.status)
    ).all()
    absence_days = 0.0
    overtime_hours = 0.0
    present_days = 0
    for st, ot, cnt in rows:
        overtime_hours += float(ot or 0)
        if st == "absent":
            absence_days += cnt
        elif st == "present":
            present_days += cnt
    return {"absence_days": absence_days, "overtime_hours": round(overtime_hours, 2),
            "present_days": present_days}


def list_attendance(db: Session, period: str, employee_id: int | None = None):
    start = f"{period}-01"
    end = _period_end(period).isoformat()
    stmt = select(models.Attendance).where(
        func.date(models.Attendance.work_date) >= start,
        func.date(models.Attendance.work_date) <= end)
    if employee_id:
        stmt = stmt.where(models.Attendance.employee_id == employee_id)
    return list(db.scalars(stmt.order_by(models.Attendance.work_date.desc())))


# ==================================================================
# السلف والقروض
# ==================================================================
def list_loans(db: Session, employee_id: int | None = None, status_: str | None = None):
    stmt = select(models.Loan)
    if employee_id:
        stmt = stmt.where(models.Loan.employee_id == employee_id)
    if status_:
        stmt = stmt.where(models.Loan.status == status_)
    return list(db.scalars(stmt.order_by(models.Loan.id.desc())))


def create_loan(db: Session, data: schemas.LoanCreate, user: dict) -> models.Loan:
    emp = get_employee(db, data.employee_id)
    inst = round(data.principal / data.installments_count, 4)
    loan = models.Loan(
        loan_no=_next_no(db, models.Loan, "LN"),
        employee_id=emp.id, kind=data.kind, principal=data.principal,
        installments_count=data.installments_count, installment_amount=inst,
        start_period=data.start_period, remaining=data.principal,
        status="pending", notes=data.notes, created_by=user.get("user_id"))
    db.add(loan)
    db.flush()
    # جدول استهلاك الأقساط (Auto Amortization Schedule)
    p = data.start_period
    for i in range(data.installments_count):
        db.add(models.LoanInstallment(loan_id=loan.id, period=p, seq=i + 1, amount=inst))
        p = _next_period(p)
    db.commit(); db.refresh(loan)
    return loan


def approve_loan(db: Session, loan_id: int, user: dict) -> models.Loan:
    loan = db.get(models.Loan, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "السلفة غير موجودة")
    if loan.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"لا يمكن اعتماد سلفة بحالة {loan.status}")
    # الفصل بين المهام: مُنشئ الطلب لا يعتمد نفسه عند توفر هوية حقيقية
    if user.get("user_id") and loan.created_by == user.get("user_id"):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "لا يجوز لنفس المستخدم أن ينشئ السلفة ويعتمدها (Segregation of Duties)")
    loan.status = "approved"
    loan.approved_by = user.get("user_id")
    db.commit(); db.refresh(loan)
    return loan


def disburse_loan(db: Session, loan_id: int, data: schemas.LoanDisburse, user: dict) -> models.Loan:
    """صرف السلفة/القرض: سند صرف نقدي من الخزينة + قيد تحويله لأصل (سلف موظفين).

    الدورة المستندية: طلب → اعتماد → سند صرف عبر الخزينة (treasury) →
    القيد المحاسبي الناتج عن السند يرحل من البنك/الصندوق، ثم نضيف قيد إعادة
    التصنيف: مدين سلف الموظفين / دائن الصندوق أو البنك، حتى يظهر الرصيد الصحيح.
    """
    loan = db.get(models.Loan, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "السلفة غير موجودة")
    if loan.status != "approved":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "يجب اعتماد السلفة أولاً قبل الصرف")
    from backend.modules.treasury import services as tre_services
    from backend.modules.treasury import models as tre_models
    v = tre_services.create_voucher(
        db,
        tre_services.schemas.VoucherCreate(
            direction="payment", pay_method=data.pay_method, amount=float(loan.principal),
            date=data.date, party_type="other",
            source_module="hr", source_ref=loan.loan_no,
            account_id=data.account_id, shift_id=data.shift_id,
            description=f"سلفة/قرض رقم {loan.loan_no} — {loan.employee.name_ar}",
        ),
        user)
    # سند الصرف رحّل تلقائيًا: مدين مصروف نثري (5300) / دائن الصندوق بقيمة السلفة.
    # بما أن السلفة أصل وليست مصروفًا، نعكس جانب المصروف في القيد نفسه ونرحل الأصل:
    #   مدين 2410 سلف الموظفين / دائن 5300 مصروفات نثرية ومتنوعة (تصفير الأثر الخاطئ)
    # ثم يبقى أثر السند الصحيح: مدين 5300 ← دائن صندوق ⇒ الصافي: دائن الصندوق + مدين سلف.
    misc = _acct(db, "5300", "مصروفات نثرية ومتنوعة", "expense")
    _journal(db, [(employee_advances_gl(db), float(loan.principal), 0),
                  (misc, 0, float(loan.principal))],
             f"إعادة تصنيف صرف سلفة {loan.loan_no} من مصروف إلى أصل (قيد تصحيحي)",
             user, loan.loan_no, data.date)
    loan.status = "paying"
    loan.disbursed_at = data.date or dt.date.today()
    loan.voucher_ref = v.voucher_no
    db.commit(); db.refresh(loan)
    return loan


def loan_out(db: Session, loan: models.Loan) -> schemas.LoanOut:
    out = schemas.LoanOut.model_validate(loan, from_attributes=True)
    out.employee_name = loan.employee.name_ar if loan.employee else None
    out.schedule = [schemas.InstallmentOut.model_validate(s, from_attributes=True)
                    for s in sorted(loan.schedule, key=lambda x: x.seq)]
    return out


def cancel_loan(db: Session, loan_id: int, reason: str, user: dict) -> models.Loan:
    """إلغاء سلفة قبل الصرف — يعكس الاعتماد ولا يمس قيودًا (لم تُنشأ بعد)."""
    loan = db.get(models.Loan, loan_id)
    if not loan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "السلفة غير موجودة")
    if loan.status in ("paying", "settled"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "لا يمكن إلغاء سلفة تم صرفها — استخدم سداد معجل")
    loan.status = "rejected"
    loan.notes = (loan.notes or "") + f" | ملغاة: {reason}"
    db.commit(); db.refresh(loan)
    return loan


def settle_loan_early(db: Session, loan_id: int, user: dict) -> models.Loan:
    """سداد معجل لسلفة قائمة — قيد: مدين الصندوق/البنك؟ لا: يُستقطع من المسير القادم
    أو يسدد نقدًا بخزينة؛ هنا نسجل استردادًا نقديًا عبر سند قبض ثم نغلق الجدول."""
    loan = db.get(models.Loan, loan_id)
    if not loan or loan.status not in ("paying",):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "السلفة ليست في حالة سداد")
    rem = float(loan.remaining)
    if rem <= 0:
        loan.status = "settled"
        db.commit(); db.refresh(loan)
        return loan
    from backend.modules.treasury import services as tre_services
    v = tre_services.create_voucher(
        db,
        tre_services.schemas.VoucherCreate(
            direction="receipt", pay_method="cash", amount=rem,
            party_type="other", source_module="hr", source_ref=loan.loan_no,
            account_id=_default_cash_account_id(db),
            description=f"سداد معجل لسلفة {loan.loan_no} — {loan.employee.name_ar}"),
        user)
    # سند القبض رحّل: مدين الصندوق / دائن "دائنون متنوعون" (2120 وسيط).
    # نغلق الوسيط مقابل أصل السلفة: مدين 2120 / دائن 2410 سلف الموظفين.
    suspense = _acct(db, "2120", "دائنون متنوعون (مقبوضات تحت التسوية)", "liability")
    _journal(db, [(suspense, rem, 0), (employee_advances_gl(db), 0, rem)],
             f"إغلاق سلفة {loan.loan_no} بالسداد المعجل", user, loan.loan_no)
    loan.repaid_amount = float(loan.principal)
    loan.remaining = 0
    loan.status = "settled"
    for s in loan.schedule:
        if s.status == "due":
            s.status = "waived"
    db.commit(); db.refresh(loan)
    return loan


def _default_cash_account_id(db: Session) -> int:
    from backend.modules.treasury import models as tre_models
    acc = db.scalar(select(tre_models.CashAccount).where(
        tre_models.CashAccount.account_kind == "cash",
        tre_models.CashAccount.is_active.is_(True)).limit(1))
    if not acc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "لا يوجد صندوق نقدي فعّال في الخزينة")
    return acc.id


def _gl_from_voucher(db, v):
    from backend.modules.treasury import models as tre_models
    acc = db.get(tre_models.CashAccount, v.account_id)
    return acc.gl_account_id if acc else None


# ==================================================================
# مسير الرواتب الشهري
# ==================================================================
def list_runs(db: Session, period: str | None = None):
    stmt = select(models.PayrollRun)
    if period:
        stmt = stmt.where(models.PayrollRun.period == period)
    return list(db.scalars(stmt.order_by(models.PayrollRun.id.desc())))


def get_run(db: Session, run_id: int) -> models.PayrollRun:
    run = db.get(models.PayrollRun, run_id)
    if not run:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "المسير غير موجود")
    return run


def create_payroll_run(db: Session, data: schemas.PayrollRunCreate, user: dict) -> models.PayrollRun:
    """يبني مسيرًا مقترحًا (draft) لكل الموظفين النشطين مع تغذية آلية من الحضور والقروض."""
    dup = db.scalar(select(models.PayrollRun).where(
        models.PayrollRun.period == data.period,
        models.PayrollRun.status.in_(["draft", "posted", "paid"])))
    if dup:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"يوجد مسير {dup.status} للفترة {data.period} برقم {dup.run_no}")
    emps = list_employees(db, branch_id=data.branch_id, include_inactive=False)
    if not emps:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "لا يوجد موظفون نشطون لهذه الفترة")

    run = models.PayrollRun(run_no=_next_no(db, models.PayrollRun, "PR"),
                            period=data.period, branch_id=data.branch_id,
                            status="draft", prepared_by=user.get("user_id"),
                            remarks=data.remarks)
    db.add(run); db.flush()

    gross_t = ded_t = net_t = 0.0
    for emp in emps:
        summ = attendance_summary(db, emp.id, data.period)
        base = float(emp.base_salary)
        allow = float(emp.allowances)
        ot_hours = summ["overtime_hours"]
        hourly = base / WORKDAYS_PER_MONTH / 8 if base else 0
        ot_amount = round(ot_hours * hourly * OVERTIME_RATE_FACTOR, 4)
        absence = summ["absence_days"]
        day_rate = base / WORKDAYS_PER_MONTH if base else 0
        absence_ded = round(absence * day_rate, 4)

        gross = round(base + allow + ot_amount - absence_ded, 4)
        ins = round(min(float(emp.insurance_rate) * base, max(gross, 0)), 4)
        tax = round(float(emp.tax_rate) * max(gross - ins, 0), 4)

        # أقساط السلف المستحقة لهذا الشهر
        loan_ded = 0.0
        due_insts = db.scalars(select(models.LoanInstallment).join(
            models.Loan, models.LoanInstallment.loan_id == models.Loan.id).where(
                models.Loan.employee_id == emp.id,
                models.LoanInstallment.period == data.period,
                models.LoanInstallment.status == "due",
                models.Loan.status.in_(["paying"]))).all()
        detail = models.PayrollDetail(
            run_id=run.id, employee_id=emp.id,
            base_salary=base, allowances=allow,
            overtime_hours=ot_hours, overtime_amount=ot_amount,
            absence_days=absence, absence_deduction=absence_ded,
            gross=gross, insurance_deduction=ins, tax_deduction=tax)
        db.add(detail); db.flush()
        for inst in due_insts:
            amt = min(float(inst.amount), max(gross - ins - tax - loan_ded, 0))
            if amt <= 0:
                continue
            loan_ded += amt
            inst.status = "deducted"
            inst.deducted_detail_id = detail.id
            inst.deducted_at = dt.datetime.utcnow()
            ln = inst.loan
            ln.repaid_amount = float(ln.repaid_amount) + amt
            ln.remaining = round(float(ln.principal) - float(ln.repaid_amount), 4)
            if ln.remaining <= 0.0001:
                ln.status = "settled"

        partial = 0.0
        deductions = round(ins + tax + loan_ded + partial, 4)
        net = round(max(gross - deductions, 0), 4)
        detail.loan_deduction = round(loan_ded, 4)
        detail.partial_deduction = partial
        detail.deductions_total = deductions
        detail.net = net
        gross_t += gross; ded_t += deductions; net_t += net

    run.employees_count = len(emps)
    run.gross_total = round(gross_t, 4)
    run.deductions_total = round(ded_t, 4)
    run.net_total = round(net_t, 4)
    db.commit(); db.refresh(run)
    return run


def adjust_payroll_detail(db: Session, detail_id: int, data: schemas.PayrollDetailAdjust,
                          user: dict) -> models.PayrollDetail:
    """تعديل سطر قبل الاعتماد — يعيد حساب الإجماليات محليًا."""
    det = db.get(models.PayrollDetail, detail_id)
    if not det:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "سطر المسير غير موجود")
    run = db.get(models.PayrollRun, det.run_id)
    if run.status != "draft":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "لا يمكن تعديل مسير بعد الاعتماد")
    if data.allowances_extra:
        det.allowances = float(det.allowances) + data.allowances_extra
    if data.overtime_amount is not None:
        det.overtime_amount = data.overtime_amount
    if data.partial_deduction is not None:
        det.partial_deduction = data.partial_deduction
    det.gross = round(float(det.base_salary) + float(det.allowances) + float(det.overtime_amount)
                      - float(det.absence_deduction), 4)
    det.deductions_total = round(float(det.insurance_deduction) + float(det.tax_deduction)
                                 + float(det.loan_deduction) + float(det.partial_deduction), 4)
    det.net = round(max(det.gross - det.deductions_total, 0), 4)
    if data.remarks:
        det.remarks = data.remarks
    # إعادة إجماليات المسير
    run.gross_total = round(sum(float(d.gross) for d in run.details), 4)
    run.deductions_total = round(sum(float(d.deductions_total) for d in run.details), 4)
    run.net_total = round(sum(float(d.net) for d in run.details), 4)
    db.commit(); db.refresh(det)
    return det


def post_payroll_run(db: Session, run_id: int, user: dict) -> models.PayrollRun:
    """اعتماد المسير → الترحيل الآلي لقيود الأستاذ العام (متطلب التكامل المالي).

    القيود الثلاثة المعيارية (بمبدأ المقابلة Accrual Basis):
    1) الاعتراف بالمصروف:
       مدين 5200 مصروف الرواتب (الإجمالي gross لكل موظف موزن على حساب القسم إن وجد)
       مدين 5210 حصة المنشأة التأمينية (نسبة مطابقة لحصة الموظف — مبسطة)
       دائن 2400 مستحقات الموظفين (net)
       دائن 2420 التأمينات مستحق دفع (insurance employee+employer)
       دائن 2430 الضرائب مستحق دفع (tax)
       دائن 2410 سلف الموظفين (loan_deduction — إطفاء الأصل بالاستقطاع)
    2) الصرف النقدي (عند mark_paid): مدين 2400 / دائن الصندوق أو البنك.
    """
    run = get_run(db, run_id)
    if run.status != "draft":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"لا يمكن اعتماد مسير بحالة {run.status}")
    if run.accrual_entry_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "المسير مرحّل سابقًا")

    totals_gross = sum(float(d.gross) for d in run.details)
    totals_net = sum(float(d.net) for d in run.details)
    totals_ins = sum(float(d.insurance_deduction) for d in run.details)
    totals_tax = sum(float(d.tax_deduction) for d in run.details)
    totals_loan = sum(float(d.loan_deduction) for d in run.details)
    totals_partial = sum(float(d.partial_deduction) for d in run.details)
    employer_share = totals_ins  # تبسيط: حصة صاحب العمل = حصة الموظف (يُعدل حسب القانون)

    # تحقق سلامة المسير قبل الترحيل: الصافي = الإجمالي − كل الاستقطاعات
    expected_net = round(totals_gross - (totals_ins + totals_tax + totals_loan + totals_partial), 4)
    if abs(expected_net - totals_net) > 0.01:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"المسير غير متسق: صافي {totals_net} ≠ المتوقع {expected_net}")

    lines: list[tuple[int, float, float]] = []
    # مدين مصروف الرواتب مفصلًا لكل قسم (إن لم يكن له حساب خاص ⇒ 5200 العام)
    per_dept: dict[int | None, float] = {}
    for d in run.details:
        emp = d.employee
        dept = emp.department_id if emp else None
        acct = None
        if emp and emp.salary_expense_account_id:
            acct = emp.salary_expense_account_id
        elif dept:
            dep = db.get(models.Department, dept)
            acct = dep.salary_expense_account_id if dep else None
        key = acct
        per_dept[key] = per_dept.get(key, 0) + float(d.gross)
    for acct_id, amt in per_dept.items():
        lines.append((acct_id or salary_expense_gl(db), round(amt, 4), 0))
    if employer_share > 0:
        lines.append((employer_insurance_gl(db), round(employer_share, 4), 0))
    if totals_net > 0:
        lines.append((payroll_payable_gl(db), 0, round(totals_net, 4)))
    if totals_ins + employer_share > 0:
        lines.append((insurance_payable_gl(db), 0, round(totals_ins + employer_share, 4)))
    if totals_tax > 0:
        lines.append((tax_payable_gl(db), 0, round(totals_tax, 4)))
    if totals_loan > 0:
        lines.append((employee_advances_gl(db), 0, round(totals_loan, 4)))
    # استقطاعات يدوية/جزاءات: تُرحل كالتزام دائن مؤقت (رواتب مدينة) حتى سدادها أو قيدها لمصروف آخر
    if totals_partial > 0:
        lines.append((payroll_payable_gl(db), 0, round(totals_partial, 4)))

    je = _journal(db, lines,
                  f"مسير رواتب {run.run_no} — فترة {run.period}",
                  user, run.run_no, _period_end(run.period))
    run.accrual_entry_id = je.id
    run.status = "posted"
    run.approved_by = user.get("user_id")
    run.approved_at = dt.datetime.utcnow()
    db.commit(); db.refresh(run)
    return run


def mark_payroll_paid(db: Session, run_id: int, data: schemas.PayrollPaymentIn,
                      user: dict) -> models.PayrollRun:
    """صرف صافي المسير عبر سند خزينة (نقدي/تحويل/شيك) + قيد مدين مستحقات الموظفين."""
    run = get_run(db, run_id)
    if run.status != "posted":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "يجب اعتماد المسير قبل الصرف")
    net = float(run.net_total)
    if net <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "لا توجد قيمة صافية للصرف")

    from backend.modules.treasury import services as tre_services
    from backend.modules.treasury import models as tre_models
    acc = db.get(tre_models.CashAccount, data.account_id)
    if not acc or not acc.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "الحساب المالي غير صحيح")
    gl = acc.gl_account_id or (cash_gl(db) if acc.account_kind == "cash" else bank_gl(db))

    # قيد الصرف: مدين 2400 مستحقات الموظفين / دائن الصندوق أو البنك
    je = _journal(db, [(payroll_payable_gl(db), net, 0), (gl, 0, net)],
                  f"صرف مسير رواتب {run.run_no} ({data.pay_method})",
                  user, run.run_no, data.date)
    run.payment_entry_id = je.id
    run.payment_voucher_ref = f"PAY-{run.run_no}"
    run.status = "paid"
    for d in run.details:
        d.paid = True
        d.payment_ref = run.payment_voucher_ref
    db.commit(); db.refresh(run)
    return run


def cancel_payroll_run(db: Session, run_id: int, reason: str, user: dict) -> models.PayrollRun:
    """إلغاء مسير معتمد قبل الصرف — يعكس قيد الاعتماد بقيد عكسي (Reversal) ويحرر الأقساط."""
    run = get_run(db, run_id)
    if run.status == "paid":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "المسير المدفوع لا يلغى — أعد القيد يدويًا مع محضر معتمد")
    if run.status == "cancelled":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "المسير ملغى مسبقًا")
    if run.accrual_entry_id:
        from backend.modules.accounting import models as acc_models
        orig = db.get(acc_models.JournalEntry, run.accrual_entry_id)
        rev_lines = [(l.account_id, float(l.credit), float(l.debit))
                     for l in orig.lines if float(l.debit) or float(l.credit)]
        if rev_lines:
            _journal(db, rev_lines, f"عكس قيد مسير ملغى {run.run_no}: {reason}",
                     user, run.run_no)
    # تحرير الأقساط التي استقطعت
    for d in run.details:
        for inst in db.scalars(select(models.LoanInstallment).where(
                models.LoanInstallment.deducted_detail_id == d.id,
                models.LoanInstallment.status == "deducted")):
            ln = inst.loan
            ln.repaid_amount = round(float(ln.repaid_amount) - float(inst.amount), 4)
            ln.remaining = round(float(ln.principal) - float(ln.repaid_amount), 4)
            if ln.status == "settled":
                ln.status = "paying"
            inst.status = "due"
            inst.deducted_detail_id = None
            inst.deducted_at = None
            d.loan_deduction = round(float(d.loan_deduction) - float(inst.amount), 4)
            d.deductions_total = round(float(d.insurance_deduction) + float(d.tax_deduction)
                                      + float(d.loan_deduction) + float(d.partial_deduction), 4)
            d.net = round(max(float(d.gross) - float(d.deductions_total), 0), 4)
    run.status = "cancelled"
    run.remarks = (run.remarks or "") + f" | ملغي: {reason}"
    db.commit(); db.refresh(run)
    return run


def run_out(db: Session, run: models.PayrollRun, with_details: bool = True) -> schemas.PayrollRunOut:
    out = schemas.PayrollRunOut.model_validate(run, from_attributes=True)
    if with_details:
        items = []
        for d in run.details:
            it = schemas.PayrollDetailOut.model_validate(d, from_attributes=True)
            emp = d.employee
            it.employee_name = emp.name_ar if emp else None
            if emp and emp.department_id:
                dep = db.get(models.Department, emp.department_id)
                it.department_name = dep.name_ar if dep else None
            items.append(it)
        out.details = sorted(items, key=lambda x: (x.department_name or "", x.employee_name or ""))
    return out


def payroll_sheet_report(db: Session, period: str) -> dict:
    """كشف الرواتب الشهري للتجميع والعرض والطباعة."""
    runs = [r for r in list_runs(db, period) if r.status in ("posted", "paid")]
    rows = []
    for r in runs:
        for d in r.details:
            emp = d.employee
            dep = emp.department if emp and emp.department_id else None
            rows.append({
                "employee_code": emp.code if emp else "",
                "employee_name": emp.name_ar if emp else "",
                "department": dep.name_ar if dep else "-",
                "job_title": emp.job_title if emp else "",
                "base": float(d.base_salary), "allowances": float(d.allowances),
                "overtime_hours": float(d.overtime_hours), "overtime_amount": float(d.overtime_amount),
                "absence_days": float(d.absence_days), "absence_deduction": float(d.absence_deduction),
                "gross": float(d.gross), "insurance": float(d.insurance_deduction),
                "tax": float(d.tax_deduction), "loans": float(d.loan_deduction),
                "partial": float(d.partial_deduction),
                "deductions": float(d.deductions_total), "net": float(d.net),
                "paid": d.paid,
            })
    tot = {k: round(sum(r[k] for r in rows), 4) for k in
           ("base", "allowances", "overtime_amount", "absence_deduction", "gross",
            "insurance", "tax", "loans", "partial", "deductions", "net")}
    return {"period": period, "rows": rows, "totals": tot,
            "count": len(rows), "runs": [run_out(db, r, with_details=False) for r in runs]}
