# -*- coding: utf-8 -*-
"""قسم المشتريات — نقاط النهاية (Router مستقل بالكامل).

مسار دورة المشتريات المتكاملة:
    /material-requests → /orders (+approve/send/close/from-mr) → /delivery-notes
    → /receipts (GRN ⇒ إضافة المخزون وقيد GRNI) → /invoices (المطابقة الثلاثية والقيد النهائي)
    → /invoices/{id}/payments أو /treasury/vouchers للسداد.
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


# ---------- 1) طلب الاحتياجات ----------
@router.post("/material-requests", response_model=ApiResponse[schemas.MROut],
             summary="إنشاء طلب احتياجات (مسودة)")
def create_mr(data: schemas.MRCreate, db: Session = Depends(get_db),
              user: dict = Depends(get_current_user)):
    mr = services.create_material_request(db, data, user)
    return ApiResponse.ok(schemas.MROut.model_validate(mr),
                          message="تم تسجيل طلب الاحتياجات (لا أثر مخزني/محاسبي له)")


@router.get("/material-requests", response_model=ApiResponse[list[schemas.MROut]],
            summary="قائمة طلبات الاحتياجات")
def list_mrs(status: str | None = None, db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.MROut.model_validate(m)
                           for m in services.list_material_requests(db, status)])


@router.post("/material-requests/{mr_id}/approve", response_model=ApiResponse[schemas.MROut],
             summary="اعتماد طلب احتياجات")
def approve_mr(mr_id: int, data: schemas.MRDecisionIn | None = None,
               db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    mr = services.approve_material_request(db, mr_id, user, data.reason if data else None)
    return ApiResponse.ok(schemas.MROut.model_validate(mr), message="تم اعتماد طلب الاحتياجات")


@router.post("/material-requests/{mr_id}/reject", response_model=ApiResponse[schemas.MROut],
             summary="رفض طلب احتياجات")
def reject_mr(mr_id: int, data: schemas.MRDecisionIn | None = None,
              db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    mr = services.reject_material_request(db, mr_id, user, data.reason if data else None)
    return ApiResponse.ok(schemas.MROut.model_validate(mr), message="تم رفض طلب الاحتياجات")


# ---------- 2) أوامر الشراء ----------
@router.post("/orders", response_model=ApiResponse[schemas.POOut], summary="إنشاء أمر شراء")
def create_po(data: schemas.POCreate,
              db: Session = Depends(get_db),
              user: dict = Depends(get_current_user)):
    po = services.create_purchase_order(db, data, user)
    return ApiResponse.ok(schemas.POOut.model_validate(po), message="تم إنشاء أمر الشراء (مسودة)")


@router.post("/material-requests/{mr_id}/convert-to-po", response_model=ApiResponse[schemas.POOut],
             summary="تحويل طلب احتياجات معتمد إلى أمر شراء (سحب تلقائي)")
def convert_mr_to_po(mr_id: int, data: schemas.POFromMRIn,
                     db: Session = Depends(get_db),
                     user: dict = Depends(get_current_user)):
    po = services.convert_mr_to_po(db, mr_id, data, user)
    return ApiResponse.ok(schemas.POOut.model_validate(po),
                          message="تم تحويل الطلب إلى أمر شراء بمراجعة الكميات تلقائيًا")


@router.get("/orders", response_model=ApiResponse[list[schemas.POOut]], summary="قائمة أوامر الشراء")
def list_pos(db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.POOut.model_validate(p) for p in services.list_purchase_orders(db)])


@router.post("/orders/{po_id}/approve", response_model=ApiResponse[schemas.POOut], summary="اعتماد أمر شراء")
def approve_po(po_id: int, db: Session = Depends(get_db)):
    po = services.approve_purchase_order(db, po_id)
    return ApiResponse.ok(schemas.POOut.model_validate(po),
                          message="تم اعتماد أمر الشراء (بلا قيد محاسبي — التزام تعاقدي)")


@router.post("/orders/{po_id}/send", response_model=ApiResponse[schemas.POOut],
             summary="إرسال أمر الشراء للمورد")
def send_po(po_id: int, db: Session = Depends(get_db)):
    po = services.send_purchase_order(db, po_id)
    return ApiResponse.ok(schemas.POOut.model_validate(po), message="تم إرسال الأمر للمورد")


@router.post("/orders/{po_id}/close", response_model=ApiResponse[schemas.POOut],
             summary="إغلاق/تنفيذ أمر شراء")
def close_po(po_id: int, db: Session = Depends(get_db)):
    po = services.close_purchase_order(db, po_id)
    return ApiResponse.ok(schemas.POOut.model_validate(po), message="تم إغلاق أمر الشراء")


# ---------- 3) إذن التوريد الوارد من المورد ----------
@router.post("/delivery-notes", response_model=ApiResponse[schemas.DNOout],
             summary="تسجيل إذن توريد وارد (مطابقة الكميات المعلنة بأمر الشراء)")
def create_dn(data: schemas.DNCreate, db: Session = Depends(get_db),
              user: dict = Depends(get_current_user)):
    dn = services.create_delivery_note(db, data, user)
    return ApiResponse.ok(schemas.DNOout.model_validate(dn),
                          message="تم تسجيل إذن التوريد والتحقق من عدم تجاوز الكميات المطلوبة")


@router.get("/delivery-notes", response_model=ApiResponse[list[schemas.DNOout]],
            summary="قائمة أذون التوريد الواردة")
def list_dns(db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.DNOout.model_validate(d) for d in services.list_delivery_notes(db)])


# ---------- 4+5) إذن الاستلام المخزني ⇒ الإضافة للمخزن ----------
@router.post("/receipts", response_model=ApiResponse[schemas.ReceiptOut],
             summary="إذن استلام على أمر شراء (فحص نوعي + إضافة المخزون + قيد GRNI)")
def create_receipt(data: schemas.ReceiptCreate,
                   db: Session = Depends(get_db),
                   user: dict = Depends(get_current_user)):
    grn = services.create_goods_receipt(db, data, user)
    return ApiResponse.ok(schemas.ReceiptOut.model_validate(grn),
                          message="تم تسجيل الاستلام وإضافة المخزون وترحيل (مدين مخزون / دائن بضاعة لم تُفوتر)")


@router.get("/receipts", response_model=ApiResponse[list[schemas.ReceiptOut]], summary="قائمة أذون الاستلام")
def list_receipts(db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.ReceiptOut.model_validate(r)
                           for r in services.list_goods_receipts(db)])


# ---------- 6+7) الفوترة والتأثير المحاسبي النهائي ----------
@router.post("/invoices", response_model=ApiResponse[schemas.PurchaseOut],
             summary="إصدار فاتورة شراء (مع السحب التلقائي من إذن الاستلام والمطابقة الثلاثية)")
def post_invoice(data: schemas.PurchaseCreate,
                 db: Session = Depends(get_db),
                 user: dict = Depends(get_current_user)):
    inv = services.post_purchase_invoice(db, data, user)
    return ApiResponse.ok(schemas.PurchaseOut.model_validate(inv),
                          message="تم ترحيل فاتورة الشراء وقيدها النهائي (GRNI + ضريبة مدخلات / المورد)")


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
