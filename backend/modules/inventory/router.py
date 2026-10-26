# -*- coding: utf-8 -*-
"""قسم المخازن — نقاط النهاية (Router مستقل بالكامل).

المسار الأساسي: /api/v1/inventory/...
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.config import get_current_user, module_deps
from backend.core.database import get_db
from backend.core.schemas import ApiResponse
from backend.modules.inventory import schemas, services

router = APIRouter(
    prefix="/inventory",
    tags=["المخازن"],
    dependencies=[Depends(get_current_user)],
)
health = router.get("/health")(module_deps("inventory"))


# ---------- الأصناف ----------
@router.get("/items", response_model=ApiResponse[list[schemas.ItemOut]], summary="قائمة الأصناف")
def list_items(search: str | None = None, db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.ItemOut.model_validate(i) for i in services.list_items(db, search)])


@router.post("/items", response_model=ApiResponse[schemas.ItemOut], summary="إضافة صنف")
def create_item(data: schemas.ItemCreate, db: Session = Depends(get_db)):
    return ApiResponse.ok(schemas.ItemOut.model_validate(services.create_item(db, data)),
                          message="تم إضافة الصنف")


@router.put("/items/{item_id}", response_model=ApiResponse[schemas.ItemOut], summary="تعديل صنف")
def update_item(item_id: int, data: schemas.ItemUpdate, db: Session = Depends(get_db)):
    return ApiResponse.ok(schemas.ItemOut.model_validate(services.update_item(db, item_id, data)))


# ---------- المخازن ----------
@router.get("/warehouses", response_model=ApiResponse[list[schemas.WarehouseOut]], summary="قائمة المخازن")
def list_warehouses(db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.WarehouseOut.model_validate(w) for w in services.list_warehouses(db)])


@router.post("/warehouses", response_model=ApiResponse[schemas.WarehouseOut], summary="إضافة مخزن")
def create_warehouse(data: schemas.WarehouseCreate, db: Session = Depends(get_db)):
    return ApiResponse.ok(schemas.WarehouseOut.model_validate(services.create_warehouse(db, data)))


# ---------- الحركات والتقارير ----------
@router.post("/movements", response_model=ApiResponse[schemas.MovementOut], summary="تسجيل حركة مخزون")
def post_movement(data: schemas.MovementCreate,
                  db: Session = Depends(get_db),
                  user: dict = Depends(get_current_user)):
    mv = services.post_movement(db, data, user)
    return ApiResponse.ok(schemas.MovementOut.model_validate(mv), message="تم تسجيل الحركة والقيود المرتبطة")


@router.get("/movements", response_model=ApiResponse[list[schemas.MovementOut]], summary="سجل الحركات")
def get_movements(item_id: int | None = None, warehouse_id: int | None = None,
                  source_doc: str | None = None, db: Session = Depends(get_db)):
    """سجل حركة تفصيلي — يمكن تصفيته بصنف/مخزن/مستند أصلي (MR / PO / STK ...)."""
    rows = services.get_movements(db, item_id, warehouse_id, source_doc)
    return ApiResponse.ok([schemas.MovementOut.model_validate(m) for m in rows])


@router.get("/reports/stock", response_model=ApiResponse[list[schemas.StockLevel]], summary="تقرير الرصيد الحالي")
def stock_report(db: Session = Depends(get_db)):
    return ApiResponse.ok(services.stock_report(db))


@router.get("/reports/valuation", summary="تقرير تقييم المخزون الدفتري (IAS 2)")
def valuation_report(db: Session = Depends(get_db)):
    """إجمالي التقييم بـ (كمية × تكلفة بالطريقة المعتمدة لكل صنف) — يجب أن يوافق حساب المخزون بالأستاذ."""
    return ApiResponse.ok(services.valuation_report(db))


# ---------- الجرد والتسويات ----------
@router.post("/counts", response_model=ApiResponse[schemas.StockCountOut], summary="إنشاء ورقة جرد")
def create_stock_count(data: schemas.StockCountCreate,
                       db: Session = Depends(get_db),
                       user: dict = Depends(get_current_user)):
    count = services.create_stock_count(db, data, user)
    return ApiResponse.ok(schemas.StockCountOut.model_validate(count),
                          message="تم إنشاء ورقة الجرد مع حساب الفروق")


@router.post("/counts/{count_id}/post", response_model=ApiResponse[schemas.StockCountOut],
             summary="اعتماد الجرد وإصدار التسويات المحاسبية")
def post_stock_count(count_id: int,
                     auto_adjustments: bool | None = None,
                     db: Session = Depends(get_db),
                     user: dict = Depends(get_current_user)):
    """اعتماد الجرد. مرر auto_adjustments=false للترحيل كمراجعة فقط دون قيود تسوية."""
    count = services.post_stock_count(db, count_id, user, auto_adjustments=auto_adjustments)
    return ApiResponse.ok(schemas.StockCountOut.model_validate(count),
                          message="تم اعتماد الجرد وإصدار حركات التسوية وقيودها")


@router.get("/counts", response_model=ApiResponse[list[schemas.StockCountOut]], summary="قائمة أوراق الجرد")
def list_stock_counts(db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.StockCountOut.model_validate(c)
                           for c in services.list_stock_counts(db)])
