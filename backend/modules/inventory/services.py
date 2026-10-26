# -*- coding: utf-8 -*-
"""قسم المخازن — منطق الأعمال + التكامل المحاسبي.

قاعدة الربط بين الأقسام: الاستيراد من backend.modules.accounting.services
فقط (طبقة خدمات)، ولا يتم الوصول لجداول قسم آخر مباشرة.
"""
from __future__ import annotations

import datetime as dt

from fastapi import HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from backend.modules.accounting import schemas as acc_schemas
from backend.modules.accounting import services as acc_services
from backend.modules.inventory import models, schemas


# ---------- الأصناف والمخازن ----------
def list_items(db: Session, search: str | None = None):
    stmt = select(models.Item).where(models.Item.is_active.is_(True))
    if search:
        like = f"%{search}%"
        stmt = stmt.where(models.Item.name_ar.like(like) | models.Item.sku.like(like))
    return list(db.scalars(stmt.order_by(models.Item.name_ar)))


def create_item(db: Session, data: schemas.ItemCreate) -> models.Item:
    if db.scalar(select(models.Item).where(models.Item.sku == data.sku)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"الكود {data.sku} موجود بالفعل")
    obj = models.Item(**data.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def update_item(db: Session, item_id: int, data: schemas.ItemUpdate) -> models.Item:
    obj = db.get(models.Item, item_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الصنف غير موجود")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return obj


def list_warehouses(db: Session):
    return list(db.scalars(select(models.Warehouse).where(models.Warehouse.is_active.is_(True))))


def create_warehouse(db: Session, data: schemas.WarehouseCreate) -> models.Warehouse:
    obj = models.Warehouse(**data.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


# ---------- الحركات ----------
def post_movement(db: Session, data: schemas.MovementCreate, user: dict) -> models.StockMovement:
    item = db.get(models.Item, data.item_id)
    if not item:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "الصنف غير موجود")
    wh = db.get(models.Warehouse, data.warehouse_id)
    if not wh:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "المخزن غير موجود")

    current = stock_qty(db, data.item_id, data.warehouse_id)
    if data.movement_type == "out" and current < data.qty:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"الرصيد غير كافٍ: المتاح {current} والمطلوب {data.qty}")

    seq = (db.scalar(select(func.count(models.StockMovement.id))) or 0) + 1
    mv = models.StockMovement(
        doc_no=f"INV-{dt.date.today():%Y%m%d}-{seq:05d}",
        created_by=user.get("user_id"),
        **data.model_dump(),
    )
    db.add(mv)
    db.flush()

    # ---- القيد المحاسبي التلقائي (تكامل بين الأقسام عبر طبقة الخدمات فقط) ----
    if item.inventory_account_id and data.unit_cost > 0:
        amount = round(float(data.qty) * float(data.unit_cost), 4)
        stock_debit = acc_schemas.JournalLineIn(account_id=item.inventory_account_id,
                                                debit=amount, credit=0)
        stock_credit = acc_schemas.JournalLineIn(account_id=item.inventory_account_id,
                                                 debit=0, credit=amount)
        counterpart_acct = item.tax_account_id or item.inventory_account_id
        counterpart = acc_schemas.JournalLineIn(
            account_id=counterpart_acct,
            debit=amount if data.movement_type == "out" else 0,
            credit=amount if data.movement_type == "in" else 0,
        )
        lines = ([stock_debit, counterpart] if data.movement_type == "in"
                 else [counterpart, stock_credit])
        entry_data = acc_schemas.JournalEntryCreate(
            date=dt.date.today(),
            description=f"قيد تلقائي — حركة مخزون {mv.doc_no} / {item.name_ar}",
            lines=lines,
        )
        acc_services.create_journal_entry(db, entry_data, user,
                                          source_module="inventory", source_ref=mv.doc_no)
    db.commit()
    db.refresh(mv)
    return mv


def stock_qty(db: Session, item_id: int, warehouse_id: int | None = None) -> float:
    signed = case(
        (models.StockMovement.movement_type.in_(["in", "adjust"]), models.StockMovement.qty),
        else_=-1 * models.StockMovement.qty,
    )
    stmt = (
        select(func.coalesce(func.sum(signed), 0))
        .where(models.StockMovement.item_id == item_id)
    )
    if warehouse_id is not None:
        stmt = stmt.where(models.StockMovement.warehouse_id == warehouse_id)
    return float(db.scalar(stmt) or 0)


def stock_report(db: Session):
    items = list(db.scalars(select(models.Item).where(models.Item.is_active.is_(True))))
    warehouses = list(db.scalars(select(models.Warehouse).where(models.Warehouse.is_active.is_(True))))
    rows = []
    for it in items:
        for wh in warehouses:
            qty = stock_qty(db, it.id, wh.id)
            if qty == 0:
                continue
            rows.append(schemas.StockLevel(
                item_id=it.id, sku=it.sku, name_ar=it.name_ar,
                warehouse_id=wh.id, warehouse_name=wh.name_ar,
                qty=qty, value=round(qty * float(it.purchase_price), 4),
                below_reorder=qty <= float(it.reorder_level),
            ))
    return rows
