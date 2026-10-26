# -*- coding: utf-8 -*-
"""قسم الموارد البشرية وشئون العاملين — نقاط النهاية (Router مستقل بالكامل).

المسار الأساسي: /api/v1/hr/...
  /hr/departments   إدارة الأقسام
  /hr/branches      إدارة الفروع
  /hr/employees     الملف الوظيفي للموظفين
  /hr/attendance    الحضور والانصراف + ملخصات شهرية
  /hr/loans         السلف والقروض ودورة الصرف والاعتماد
  /hr/payrolls      مسير الرواتب: توليد → تعديل → اعتماد (ترحيل آلي) → صرف → إلغاء
  /hr/reports       كشف المرتبات الشهري (HTML للطباعة + JSON)
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.core.config import get_current_user, module_deps
from backend.core.database import get_db
from backend.core.schemas import ApiResponse
from backend.modules.hr import models, schemas, services

router = APIRouter(
    prefix="/hr",
    tags=["الموارد البشرية"],
    dependencies=[Depends(get_current_user)],
)
health = router.get("/health")(module_deps("hr"))

templates = Jinja2Templates(directory="backend/templates")


def _fmt(v):
    if v is None:
        return "—"
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def _pct(v):
    if v is None:
        return "—"
    return f"{v:+.1f}%"


templates.env.filters["money"] = _fmt
templates.env.filters["pct"] = _pct


# ==================== الأقسام والفروع ====================
@router.get("/departments", response_model=ApiResponse[list[schemas.DepartmentOut]], summary="قائمة الأقسام")
def list_departments(db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.DepartmentOut.model_validate(x)
                           for x in services.list_departments(db)])


@router.post("/departments", response_model=ApiResponse[schemas.DepartmentOut], summary="إضافة قسم")
def create_department(data: schemas.DepartmentCreate, db: Session = Depends(get_db)):
    return ApiResponse.ok(schemas.DepartmentOut.model_validate(services.create_department(db, data)),
                          message="تم إضافة القسم")


@router.get("/branches", response_model=ApiResponse[list[schemas.BranchOut]], summary="قائمة الفروع")
def list_branches(db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.BranchOut.model_validate(x)
                           for x in services.list_branches(db)])


@router.post("/branches", response_model=ApiResponse[schemas.BranchOut], summary="إضافة فرع")
def create_branch(data: schemas.BranchCreate, db: Session = Depends(get_db)):
    return ApiResponse.ok(schemas.BranchOut.model_validate(services.create_branch(db, data)),
                          message="تم إضافة الفرع")


# ==================== الموظفون ====================
@router.get("/employees", response_model=ApiResponse[list[schemas.EmployeeOut]], summary="قائمة الموظفين")
def list_employees(search: str | None = None,
                   department_id: int | None = None,
                   branch_id: int | None = None,
                   include_inactive: bool = False,
                   db: Session = Depends(get_db)):
    rows = services.list_employees(db, search, department_id, branch_id, include_inactive)
    return ApiResponse.ok([services.employee_out(db, e) for e in rows])


@router.get("/employees/{employee_id}", response_model=ApiResponse[schemas.EmployeeOut], summary="ملف موظف")
def get_employee(employee_id: int, db: Session = Depends(get_db)):
    return ApiResponse.ok(services.employee_out(db, services.get_employee(db, employee_id)))


@router.post("/employees", response_model=ApiResponse[schemas.EmployeeOut], summary="تعيين موظف جديد")
def create_employee(data: schemas.EmployeeCreate, db: Session = Depends(get_db)):
    emp = services.create_employee(db, data)
    return ApiResponse.ok(services.employee_out(db, emp), message="تم تسجيل الموظف")


@router.put("/employees/{employee_id}", response_model=ApiResponse[schemas.EmployeeOut], summary="تعديل بيانات موظف")
def update_employee(employee_id: int, data: schemas.EmployeeUpdate, db: Session = Depends(get_db)):
    emp = services.update_employee(db, employee_id, data)
    return ApiResponse.ok(services.employee_out(db, emp))


# ==================== الحضور والانصراف ====================
@router.post("/attendance", response_model=ApiResponse[schemas.AttendanceOut], summary="تسجيل حضور/انصراف")
def upsert_attendance(data: schemas.AttendanceIn,
                      db: Session = Depends(get_db),
                      user: dict = Depends(get_current_user)):
    rec = services.upsert_attendance(db, data, user)
    out = schemas.AttendanceOut.model_validate(rec)
    out.employee_name = rec.employee.name_ar if rec.employee else None
    return ApiResponse.ok(out, message="تم تسجيل الحضور")


@router.get("/attendance", response_model=ApiResponse[list[schemas.AttendanceOut]], summary="سجل الحضور الشهري")
def list_attendance(period: str = Query(pattern=r"^\d{4}-\d{2}$"),
                    employee_id: int | None = None, db: Session = Depends(get_db)):
    rows = []
    for r in services.list_attendance(db, period, employee_id):
        o = schemas.AttendanceOut.model_validate(r)
        o.employee_name = r.employee.name_ar if r.employee else None
        rows.append(o)
    return ApiResponse.ok(rows)


@router.get("/attendance/summary", response_model=ApiResponse, summary="ملخص الشهر (غياب/إضافي)")
def attendance_summary(employee_id: int, period: str = Query(pattern=r"^\d{4}-\d{2}$"),
                       db: Session = Depends(get_db)):
    return ApiResponse.ok(services.attendance_summary(db, employee_id, period))


# ==================== السلف والقروض ====================
@router.get("/loans", response_model=ApiResponse[list[schemas.LoanOut]], summary="قائمة السلف والقروض")
def list_loans(employee_id: int | None = None, status: str | None = None,
               db: Session = Depends(get_db)):
    return ApiResponse.ok([services.loan_out(db, l)
                           for l in services.list_loans(db, employee_id, status)])


@router.post("/loans", response_model=ApiResponse[schemas.LoanOut], summary="طلب سلفة/قرض جديد")
def create_loan(data: schemas.LoanCreate, db: Session = Depends(get_db),
                user: dict = Depends(get_current_user)):
    loan = services.create_loan(db, data, user)
    return ApiResponse.ok(services.loan_out(db, loan), message="تم إنشاء الطلب (بانتظار الاعتماد)")


@router.post("/loans/{loan_id}/approve", response_model=ApiResponse[schemas.LoanOut], summary="اعتماد السلفة")
def approve_loan(loan_id: int, db: Session = Depends(get_db),
                 user: dict = Depends(get_current_user)):
    loan = services.approve_loan(db, loan_id, user)
    return ApiResponse.ok(services.loan_out(db, loan), message="تم اعتماد السلفة")


@router.post("/loans/{loan_id}/disburse", response_model=ApiResponse[schemas.LoanOut],
             summary="صرف السلفة عبر الخزينة")
def disburse_loan(loan_id: int, data: schemas.LoanDisburse, db: Session = Depends(get_db),
                  user: dict = Depends(get_current_user)):
    loan = services.disburse_loan(db, loan_id, data, user)
    return ApiResponse.ok(services.loan_out(db, loan), message="تم الصرف وتوليد القيود")


@router.post("/loans/{loan_id}/cancel", response_model=ApiResponse[schemas.LoanOut], summary="إلغاء سلفة قبل الصرف")
def cancel_loan(loan_id: int, reason: str = "بدون سبب", db: Session = Depends(get_db),
                user: dict = Depends(get_current_user)):
    loan = services.cancel_loan(db, loan_id, reason, user)
    return ApiResponse.ok(services.loan_out(db, loan), message="تم إلغاء الطلب")


@router.post("/loans/{loan_id}/settle", response_model=ApiResponse[schemas.LoanOut], summary="سداد معجل للسلفة")
def settle_loan(loan_id: int, db: Session = Depends(get_db),
                user: dict = Depends(get_current_user)):
    loan = services.settle_loan_early(db, loan_id, user)
    return ApiResponse.ok(services.loan_out(db, loan), message="تم السداد المعجل وإغلاق الجدول")


# ==================== مسير الرواتب ====================
@router.get("/payrolls", response_model=ApiResponse[list[schemas.PayrollRunOut]], summary="قائمة المسيرات")
def list_runs(period: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
              db: Session = Depends(get_db)):
    runs = services.list_runs(db, period)
    return ApiResponse.ok([services.run_out(db, r, with_details=False) for r in runs])


@router.get("/payrolls/{run_id}", response_model=ApiResponse[schemas.PayrollRunOut], summary="تفاصيل مسير")
def get_run(run_id: int, db: Session = Depends(get_db)):
    return ApiResponse.ok(services.run_out(db, services.get_run(db, run_id)))


@router.post("/payrolls", response_model=ApiResponse[schemas.PayrollRunOut],
             summary="توليد مسير رواتب الفترة (draft)")
def create_run(data: schemas.PayrollRunCreate, db: Session = Depends(get_db),
               user: dict = Depends(get_current_user)):
    run = services.create_payroll_run(db, data, user)
    return ApiResponse.ok(services.run_out(db, run),
                          message=f"تم توليد مسير {run.period} لعدد {run.employees_count} موظفًا")


@router.put("/payrolls/details/{detail_id}", response_model=ApiResponse[schemas.PayrollDetailOut],
            summary="تعديل سطر في المسير قبل الاعتماد")
def adjust_detail(detail_id: int, data: schemas.PayrollDetailAdjust, db: Session = Depends(get_db),
                  user: dict = Depends(get_current_user)):
    det = services.adjust_payroll_detail(db, detail_id, data, user)
    out = schemas.PayrollDetailOut.model_validate(det)
    out.employee_name = det.employee.name_ar if det.employee else None
    return ApiResponse.ok(out)


@router.post("/payrolls/{run_id}/post", response_model=ApiResponse[schemas.PayrollRunOut],
             summary="اعتماد المسير والترحيل الآلي للأستاذ العام")
def post_run(run_id: int, db: Session = Depends(get_db),
             user: dict = Depends(get_current_user)):
    run = services.post_payroll_run(db, run_id, user)
    return ApiResponse.ok(services.run_out(db, run),
                          message="تم اعتماد المسير وتوليد قيود الاعتراف بالمصروف")


@router.post("/payrolls/{run_id}/pay", response_model=ApiResponse[schemas.PayrollRunOut],
             summary="صرف صافي المسير (نقدي/تحويل/شيك)")
def pay_run(run_id: int, data: schemas.PayrollPaymentIn, db: Session = Depends(get_db),
            user: dict = Depends(get_current_user)):
    run = services.mark_payroll_paid(db, run_id, data, user)
    return ApiResponse.ok(services.run_out(db, run), message="تم الصرف وتوليد قيد النقدية")


@router.post("/payrolls/{run_id}/cancel", response_model=ApiResponse[schemas.PayrollRunOut],
             summary="إلغاء مسير معتمد (بقيد عكسي)")
def cancel_run(run_id: int, reason: str = "بدون سبب", db: Session = Depends(get_db),
               user: dict = Depends(get_current_user)):
    run = services.cancel_payroll_run(db, run_id, reason, user)
    return ApiResponse.ok(services.run_out(db, run), message="تم الإلغاء وترحيل القيد العكسي")


# ==================== التقارير ====================
@router.get("/reports/payroll-sheet", response_model=ApiResponse, summary="كشف الرواتب (JSON)")
def payroll_sheet_json(period: str = Query(pattern=r"^\d{4}-\d{2}$"), db: Session = Depends(get_db)):
    return ApiResponse.ok(services.payroll_sheet_report(db, period))


@router.get("/reports/payroll-sheet/print", response_class=HTMLResponse,
            summary="طباعة كشف الرواتب الشهري")
def payroll_sheet_print(request: Request,
                        period: str = Query(pattern=r"^\d{4}-\d{2}$"),
                        print_auto: int = 0,
                        db: Session = Depends(get_db)):
    rep = services.payroll_sheet_report(db, period)
    return templates.TemplateResponse("reports/payroll_sheet.html", {
        "request": request,
        "title": f"كشف رواتب — {period}",
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "print_auto": bool(print_auto),
        **rep,
    })
