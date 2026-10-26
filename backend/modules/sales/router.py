# -*- coding: utf-8 -*-
"""قسم المبيعات — نقاط النهاية (Router مستقل بالكامل).

المسار الأساسي: /api/v1/sales/...
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.config import get_current_user, module_deps
from backend.core.database import get_db
from backend.core.schemas import ApiResponse
from backend.modules.sales import schemas, services

router = APIRouter(
    prefix="/sales",
    tags=["المبيعات"],
    dependencies=[Depends(get_current_user)],
)
health = router.get("/health")(module_deps("sales"))


@router.get("/customers", response_model=ApiResponse[list[schemas.CustomerOut]], summary="قائمة العملاء")
def list_customers(search: str | None = None, db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.CustomerOut.model_validate(c)
                           for c in services.list_customers(db, search)])


@router.post("/customers", response_model=ApiResponse[schemas.CustomerOut], summary="إضافة عميل")
def create_customer(data: schemas.CustomerCreate, db: Session = Depends(get_db)):
    return ApiResponse.ok(schemas.CustomerOut.model_validate(services.create_customer(db, data)),
                          message="تم إضافة العميل")


@router.post("/invoices", response_model=ApiResponse[schemas.InvoiceOut], summary="إصدار فاتورة بيع")
def post_invoice(data: schemas.InvoiceCreate,
                 db: Session = Depends(get_db),
                 user: dict = Depends(get_current_user)):
    inv = services.post_invoice(db, data, user)
    return ApiResponse.ok(schemas.InvoiceOut.model_validate(inv),
                          message="تم إصدار الفاتورة مع الحركات والقيود المحاسبية")


@router.get("/invoices", response_model=ApiResponse[list[schemas.InvoiceOut]], summary="قائمة الفواتير")
def list_invoices(db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.InvoiceOut.model_validate(i) for i in services.list_invoices(db)])


@router.post("/invoices/{invoice_id}/payments", response_model=ApiResponse[schemas.InvoiceOut],
             summary="تسجيل دفعة على فاتورة")
def register_payment(invoice_id: int, data: schemas.PaymentIn, db: Session = Depends(get_db)):
    inv = services.register_payment(db, invoice_id, data.amount)
    return ApiResponse.ok(schemas.InvoiceOut.model_validate(inv), message="تم تسجيل الدفعة")
