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


@router.get("/reports/stock", response_model=ApiResponse[list[schemas.StockLevel]], summary="تقرير الرصيد الحالي")
def stock_report(db: Session = Depends(get_db)):
    return ApiResponse.ok(services.stock_report(db))
