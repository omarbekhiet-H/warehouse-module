# -*- coding: utf-8 -*-
"""قسم الموارد البشرية — نقاط النهاية (Router مستقل بالكامل).

المسار الأساسي: /api/v1/hr/...
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.config import get_current_user, module_deps
from backend.core.database import get_db
from backend.core.schemas import ApiResponse
from backend.modules.hr import schemas, services

router = APIRouter(
    prefix="/hr",
    tags=["الموارد البشرية"],
    dependencies=[Depends(get_current_user)],
)
health = router.get("/health")(module_deps("hr"))


# ---------- الموظفون ----------
@router.get("/employees", response_model=ApiResponse[list[schemas.EmployeeOut]], summary="قائمة الموظفين")
def list_employees(search: str | None = None, db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.EmployeeOut.model_validate(e)
                           for e in services.list_employees(db, search)])


@router.post("/employees", response_model=ApiResponse[schemas.EmployeeOut], summary="إضافة موظف")
def create_employee(data: schemas.EmployeeCreate, db: Session = Depends(get_db)):
    return ApiResponse.ok(schemas.EmployeeOut.model_validate(services.create_employee(db, data)),
                          message="تم إضافة الموظف")


@router.put("/employees/{employee_id}", response_model=ApiResponse[schemas.EmployeeOut], summary="تعديل موظف")
def update_employee(employee_id: int, data: schemas.EmployeeUpdate, db: Session = Depends(get_db)):
    return ApiResponse.ok(schemas.EmployeeOut.model_validate(
        services.update_employee(db, employee_id, data)))


# ---------- الرواتب ----------
@router.post("/payrolls", response_model=ApiResponse[schemas.PayrollOut], summary="تصفية راتب موظف")
def run_payroll(data: schemas.PayrollCreate,
                db: Session = Depends(get_db),
                user: dict = Depends(get_current_user)):
    run = services.run_payroll(db, data, user)
    return ApiResponse.ok(schemas.PayrollOut.model_validate(run),
                          message="تم تصفية الراتب وتوليد القيد المحاسبي")


@router.get("/payrolls", response_model=ApiResponse, summary="تقرير الرواتب حسب الفترة")
def payroll_report(period: str | None = None, db: Session = Depends(get_db)):
    return ApiResponse.ok(services.payroll_report(db, period))
