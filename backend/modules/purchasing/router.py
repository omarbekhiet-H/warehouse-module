# -*- coding: utf-8 -*-
"""قسم المشتريات — نقاط النهاية (Router مستقل بالكامل).

المسار الأساسي: /api/v1/purchasing/...
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.config import get_current_user, module_deps
from backend.core.database import get_db
from backend.core.schemas import ApiResponse
from backend.modules.purchasing import schemas, services

router = APIRouter(
    prefix="/purchasing",
    tags=["المشتريات"],
    dependencies=[Depends(get_current_user)],
)
health = router.get("/health")(module_deps("purchasing"))


@router.get("/suppliers", response_model=ApiResponse[list[schemas.SupplierOut]], summary="قائمة الموردين")
def list_suppliers(search: str | None = None, db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.SupplierOut.model_validate(s)
                           for s in services.list_suppliers(db, search)])


@router.post("/suppliers", response_model=ApiResponse[schemas.SupplierOut], summary="إضافة مورد")
def create_supplier(data: schemas.SupplierCreate, db: Session = Depends(get_db)):
    return ApiResponse.ok(schemas.SupplierOut.model_validate(services.create_supplier(db, data)),
                          message="تم إضافة المورد")


@router.post("/invoices", response_model=ApiResponse[schemas.PurchaseOut], summary="إصدار فاتورة شراء")
def post_invoice(data: schemas.PurchaseCreate,
                 db: Session = Depends(get_db),
                 user: dict = Depends(get_current_user)):
    inv = services.post_purchase_invoice(db, data, user)
    return ApiResponse.ok(schemas.PurchaseOut.model_validate(inv),
                          message="تم إصدار فاتورة الشراء مع الحركات والقيود المحاسبية")


# ---------- أوامر الشراء ----------
@router.post("/orders", response_model=ApiResponse[schemas.POOut], summary="إنشاء أمر شراء")
def create_po(data: schemas.POCreate,
              db: Session = Depends(get_db),
              user: dict = Depends(get_current_user)):
    po = services.create_purchase_order(db, data, user)
    return ApiResponse.ok(schemas.POOut.model_validate(po), message="تم إنشاء أمر الشراء (مسودة)")


@router.get("/orders", response_model=ApiResponse[list[schemas.POOut]], summary="قائمة أوامر الشراء")
def list_pos(db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.POOut.model_validate(p) for p in services.list_purchase_orders(db)])


@router.post("/orders/{po_id}/approve", response_model=ApiResponse[schemas.POOut], summary="اعتماد أمر شراء")
def approve_po(po_id: int, db: Session = Depends(get_db)):
    po = services.approve_purchase_order(db, po_id)
    return ApiResponse.ok(schemas.POOut.model_validate(po), message="تم اعتماد أمر الشراء")


# ---------- أذون الاستلام ----------
@router.post("/receipts", response_model=ApiResponse[schemas.ReceiptOut], summary="إذن استلام على أمر شراء")
def create_receipt(data: schemas.ReceiptCreate,
                   db: Session = Depends(get_db),
                   user: dict = Depends(get_current_user)):
    grn = services.create_goods_receipt(db, data, user)
    return ApiResponse.ok(schemas.ReceiptOut.model_validate(grn),
                          message="تم تسجيل إذن الاستلام وإضافة المخزون بحركة مرتبطة به")


@router.get("/receipts", response_model=ApiResponse[list[schemas.ReceiptOut]], summary="قائمة أذون الاستلام")
def list_receipts(db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.ReceiptOut.model_validate(r)
                           for r in services.list_goods_receipts(db)])


@router.get("/invoices", response_model=ApiResponse[list[schemas.PurchaseOut]], summary="قائمة فواتير الشراء")
def list_invoices(db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.PurchaseOut.model_validate(i) for i in services.list_invoices(db)])


@router.post("/invoices/{invoice_id}/payments", response_model=ApiResponse[schemas.PurchaseOut],
             summary="تسجيل دفعة لمورد")
def register_payment(invoice_id: int, data: schemas.PaymentIn,
                     db: Session = Depends(get_db),
                     user: dict = Depends(get_current_user)):
    inv = services.register_payment(db, invoice_id, data.amount, user)
    return ApiResponse.ok(schemas.PurchaseOut.model_validate(inv), message="تم تسجيل الدفعة مع قيدها المحاسبي")
