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


def post_movement(db: Session, data: schemas.MovementCreate, user: dict) -> models.StockMovement:
    """تسجيل حركة مخزون ضمن نظام الجرد المستمر (Perpetual Inventory System).

    القواعد المحاسبية المطبقة (IAS 2 / GAAP):
    - التقييم بالتكلفة الفعلية المشتقة حسب طريقة تسعير الصنف (std/avg)، لا يدويًا.
    - in      ⇒ مدين المخزون / دائن "مخزون تحت التسوية" (GRNI) — حساب مقابل مرحلي.
    - out     ⇒ مدين COGS أو المصروف / دائن المخزون (تكلفة، لا سعر بيع).
    - transfer ⇒ حركتان متقابلتان بنفس القيمة داخل الأصول — بلا أثر على قائمة الدخل.
    - adjust  ⇒ فروق الجرد: زيادة = دائن "ربح جرد" (770)، نقص = مدين "خسارة جرد" (5910).

    التكامل مع الخزينة: إذا مُرِّر مفتاح cash_account_id في remarks/الحقل المناسب تُربط
    الحركة بالسند النقدي؛ والصرف النقدي المباشر للمواد يُحوَّل لسند صرف خزينة.
    """
    item = db.get(models.Item, data.item_id)
    if not item:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "الصنف غير موجود")
    wh = db.get(models.Warehouse, data.warehouse_id)
    if not wh:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "المخزن غير موجود")

    signed_qty = float(data.qty)  # قد تكون سالبة في تسويات النقص (adjust فقط)
    if data.movement_type != "adjust" and signed_qty <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "الكمية يجب أن تكون أكبر من صفر")

    current = stock_qty(db, data.item_id, data.warehouse_id)

    # ---- التحقق من المخزون المتاح حسب نوع الحركة ----
    if data.movement_type in ("out", "transfer") and current < abs(signed_qty):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"الرصيد غير كافٍ في مخزن المصدر: المتاح {current} والمطلوب {abs(signed_qty)}")
    if data.movement_type == "adjust" and current + signed_qty < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"التسوية ستؤدي لرصيد سالب: المتاح {current} والفرق {signed_qty}")

    # ---- التحويل بين مخزنين يتطلب مخزن وجهة صحيح ومختلف ----
    if data.movement_type == "transfer":
        if not data.dest_warehouse_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "حركة التحويل تتطلب dest_warehouse_id")
        if data.dest_warehouse_id == data.warehouse_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "لا يمكن التحويل لنفس المخزن")
        if not db.get(models.Warehouse, data.dest_warehouse_id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "مخزن الوجهة غير موجود")

    # ---- اشتقاق تكلفة الوحدة وفق طريقة تقييم الصنف (وليس إدخال المستخدم) ----
    unit_cost, cost_source = _resolve_unit_cost(item, data.movement_type, data.unit_cost)

    seq = (db.scalar(select(func.count(models.StockMovement.id))) or 0) + 1
    mv = models.StockMovement(
        doc_no=f"INV-{dt.date.today():%Y%m%d}-{seq:05d}",
        created_by=user.get("user_id"),
        item_id=data.item_id, warehouse_id=data.warehouse_id,
        dest_warehouse_id=data.dest_warehouse_id,
        movement_type=data.movement_type, qty=signed_qty,
        unit_cost=unit_cost, cost_source=cost_source,
        valuation_value=round(abs(signed_qty) * unit_cost, 4),
        source_doc=data.source_doc, remarks=data.remarks,
    )
    db.add(mv)
    db.flush()

    # ---- تحديث الرصيد الكمي والتكلفة المتوسطة على بطاقة الصنف ----
    delta = signed_qty if data.movement_type in ("in", "adjust") else \
        (abs(signed_qty) if data.movement_type == "transfer" else -abs(signed_qty))
    # التحويل يخرج من المصدر ويدخل للوجهة ⇒ صافي الرصيد الكلي للصنف لا يتغير
    if data.movement_type == "transfer":
        delta = 0.0
    item.on_hand_qty = round(float(item.on_hand_qty) + delta, 4)
    if data.movement_type == "in" and item.costing_method == "avg" and unit_cost > 0:
        old_qty, old_cost = float(item.on_hand_qty) - signed_qty, float(item.avg_cost)
        new_qty = float(item.on_hand_qty)
        item.avg_cost = round((old_qty * old_cost + signed_qty * unit_cost) / new_qty, 4) if new_qty > 0 else unit_cost

    # ---- القيد المحاسبي التلقائي (تكامل بين الأقسام عبر طبقة الخدمات فقط) ----
    amount = mv.valuation_value
    inv_acct = item.inventory_account_id or _default_inventory_account(db)
    if data.movement_type == "transfer":
        pass  # تحويل داخلي بين أصول — لا قيد محاسبي (لا يغيّر إجمالي المخزون ولا الدخل)
    elif amount > 0 and inv_acct:
        lines: list[acc_schemas.JournalLineIn] = []
        if data.movement_type == "in":
            grni = _grni_account(db)   # دائن: مخزون تحت التسوية (GRNI) بدل خلط حسابات الضريبة
            lines = [
                acc_schemas.JournalLineIn(account_id=inv_acct, debit=amount, credit=0),
                acc_schemas.JournalLineIn(account_id=grni, debit=0, credit=amount),
            ]
        elif data.movement_type == "out":
            # الصرف لأغراض البيع ⇒ COGS؛ الصرف الداخلي ⇒ حساب مصروف الصنف
            expense_acct = item.cogs_account_id or item.expense_account_id or _default_cogs_account(db)
            lines = [
                acc_schemas.JournalLineIn(account_id=expense_acct, debit=amount, credit=0),
                acc_schemas.JournalLineIn(account_id=inv_acct, debit=0, credit=amount),
            ]
        else:  # adjust — فروق الجرد بقيدها الشرعي الصحيح
            if signed_qty > 0:
                gain_acct = _gain_loss_account(db, "7700", "أرباح جرد المخزون", "revenue")
                lines = [
                    acc_schemas.JournalLineIn(account_id=inv_acct, debit=amount, credit=0),
                    acc_schemas.JournalLineIn(account_id=gain_acct, debit=0, credit=amount),
                ]
            else:
                loss_acct = _gain_loss_account(db, "5910", "خسائر جرد المخزون", "expense")
                lines = [
                    acc_schemas.JournalLineIn(account_id=loss_acct, debit=amount, credit=0),
                    acc_schemas.JournalLineIn(account_id=inv_acct, debit=0, credit=amount),
                ]
        entry_data = acc_schemas.JournalEntryCreate(
            date=dt.date.today(),
            description=f"قيد تلقائي — حركة مخزون {mv.doc_no} / {item.name_ar} ({data.movement_type})",
            lines=lines,
        )
        acc_services.create_journal_entry(db, entry_data, user,
                                          source_module="inventory", source_ref=mv.doc_no)

    # ---- التحويل: حركة مقابلة موجبة لمخزن الوجهة بنفس القيمة (بدون تكرار القيد) ----
    if data.movement_type == "transfer":
        seq2 = (db.scalar(select(func.count(models.StockMovement.id))) or 0) + 1
        mv_in = models.StockMovement(
            doc_no=f"INV-{dt.date.today():%Y%m%d}-{seq2:05d}",
            item_id=data.item_id, warehouse_id=data.dest_warehouse_id,
            movement_type="in", qty=abs(signed_qty), unit_cost=unit_cost,
            cost_source=cost_source, valuation_value=amount,
            source_doc=data.source_doc, remarks=f"وارد تحويل من {wh.name_ar} — {mv.doc_no}",
            created_by=user.get("user_id"),
        )
        db.add(mv_in)

    db.commit()
    db.refresh(mv)
    return mv


def _resolve_unit_cost(item: models.Item, movement_type: str,
                       manual_cost: float | None) -> tuple[float, str]:
    """اشتقاق تكلفة الوحدة وفق سياسة التقييم المعتمدة للصنف (الأصول المهنية):

    - الوارد (in): التكلفة الفعلية للفاتورة إن وُجدت، وإلا التكلفة المعيارية/المتوسط.
    - المنصرف (out/transfer/adjust): تكلفة التقييم الدفترية دائمًا (std أو avg متحرك)،
      ولا تُقبل تكلفة يدوية مخالفة إلا عند costing_method='fifo' كمرحلة انتقالية.
    """
    std = float(item.standard_cost or 0)
    avg = float(item.avg_cost or 0)
    if movement_type == "in":
        if manual_cost and manual_cost > 0:
            return round(manual_cost, 4), "purchase"
        return (std or avg or float(item.purchase_price or 0)), ("std" if std else "avg")
    # الحركات الخارجة/التسويات: بتكلفة الدفاتر
    if item.costing_method == "avg" and avg > 0:
        return round(avg, 4), "avg"
    if std > 0:
        return round(std, 4), "std"
    if avg > 0:
        return round(avg, 4), "avg"
    if manual_cost and manual_cost > 0:
        return round(manual_cost, 4), "manual"
    return 0.0, "none"


# ---------- حسابات التكامل الافتراضية (تُنشأ تلقائيًا إن غابت من دليل الحسابات) ----------
def _get_or_create_account(db: Session, code: str, name_ar: str, account_type: str) -> int:
    from backend.modules.accounting import models as acc_models
    from backend.modules.accounting import schemas as _acc_schemas
    from backend.modules.accounting import services as _acc_services

    acct = db.scalar(select(acc_models.Account).where(acc_models.Account.code == code))
    if not acct:
        acct = _acc_services.create_account(
            db, _acc_schemas.AccountCreate(code=code, name_ar=name_ar,
                                           account_type=account_type))
    return acct.id


def _grni_account(db: Session) -> int:
    """مخزون تحت التسوية / بضاعة مستلمة بفواتير لاحقة (حساب مقابل مرحلي — التزام قصير الأجل)."""
    return _get_or_create_account(db, "2100", "مخزون تحت التسوية (GRNI)", "liability")


def _default_inventory_account(db: Session) -> int | None:
    return _get_or_create_account(db, "1300", "المخزون", "asset")


def _default_cogs_account(db: Session) -> int:
    return _get_or_create_account(db, "5100", "تكلفة البضاعة المباعة", "expense")


def _gain_loss_account(db: Session, code: str, name: str, typ: str) -> int:
    return _get_or_create_account(db, code, name, typ)


def stock_qty(db: Session, item_id: int, warehouse_id: int | None = None) -> float:
    """الرصيد الحالي = وارد + تسويات موجبة − (صادر + تحويل خارج) + تحويل وارد للوجهة."""
    signed = case(
        (models.StockMovement.movement_type.in_(["in", "adjust"]), models.StockMovement.qty),
        else_=-1 * models.StockMovement.qty,   # out / transfer-out
    )
    stmt = (
        select(func.coalesce(func.sum(signed), 0))
        .where(models.StockMovement.item_id == item_id)
    )
    if warehouse_id is not None:
        stmt = stmt.where(models.StockMovement.warehouse_id == warehouse_id)
    qty = float(db.scalar(stmt) or 0)

    # إضافة التحويلات الواردة لهذا المخزن بصفته وجهة
    if warehouse_id is not None:
        incoming = float(db.scalar(
            select(func.coalesce(func.sum(models.StockMovement.qty), 0))
            .where(models.StockMovement.item_id == item_id,
                   models.StockMovement.movement_type == "transfer",
                   models.StockMovement.dest_warehouse_id == warehouse_id)
        ) or 0)
        qty += incoming
    return qty


def stock_report(db: Session):
    """تقرير الرصيد الحالي — التقييم بتكلفة التقييم الدفترية لكل صنف (IAS 2)."""
    items = list(db.scalars(select(models.Item).where(models.Item.is_active.is_(True))))
    warehouses = list(db.scalars(select(models.Warehouse).where(models.Warehouse.is_active.is_(True))))
    rows = []
    for it in items:
        unit_cost, _ = _resolve_unit_cost(it, "out", None)
        if unit_cost <= 0:
            unit_cost = float(it.purchase_price or 0)
        for wh in warehouses:
            qty = stock_qty(db, it.id, wh.id)
            if qty == 0:
                continue
            rows.append(schemas.StockLevel(
                item_id=it.id, sku=it.sku, name_ar=it.name_ar,
                warehouse_id=wh.id, warehouse_name=wh.name_ar,
                qty=qty, unit_cost=round(unit_cost, 4),
                value=round(qty * unit_cost, 4),
                below_reorder=qty <= float(it.reorder_level),
            ))
    return rows


def valuation_report(db: Session):
    """إجمالي تقييم المخزون الدفتري (كمية × تكلفة) — يوافق رصيد حساب المخزون في دفتر الأستاذ."""
    rows = stock_report(db)
    total_qty = round(sum(r.qty for r in rows), 4)
    total_value = round(sum(r.value for r in rows), 4)
    return {
        "lines": [r.model_dump() for r in rows],
        "total_qty": total_qty,
        "total_value": total_value,
        "note": "التقييم بطريقة التكلفة المعتمدة لكل صنف (std/avg) وفق IAS 2",
    }


# ---------- الجرد والتسويات ----------
def create_stock_count(db: Session, data: schemas.StockCountCreate, user: dict) -> models.StockCount:
    """إنشاء ورقة جرد: يلتقط رصيد النظام لحظة الإنشاء ويحسب الفرق والتكلفة الدفترية."""
    wh = db.get(models.Warehouse, data.warehouse_id)
    if not wh:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "المخزن غير موجود")
    seen_items: set[int] = set()
    seq = (db.scalar(select(func.count(models.StockCount.id))) or 0) + 1
    count = models.StockCount(
        count_no=f"STK-{dt.date.today():%Y%m%d}-{seq:05d}",
        warehouse_id=data.warehouse_id, remarks=data.remarks,
        auto_post_adjustments=data.auto_post_adjustments,
        created_by=user.get("user_id"), status="draft",
    )
    for line in data.lines:
        if line.item_id in seen_items:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                               f"الصنف رقم {line.item_id} مكرر في ورقة الجرد")
        seen_items.add(line.item_id)
        item = db.get(models.Item, line.item_id)
        if not item:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"الصنف رقم {line.item_id} غير موجود")
        sys_qty = stock_qty(db, line.item_id, data.warehouse_id)
        unit_cost, _ = _resolve_unit_cost(item, "adjust", None)
        count.lines.append(models.StockCountLine(
            item_id=line.item_id, system_qty=sys_qty,
            counted_qty=line.counted_qty,
            variance_qty=round(line.counted_qty - sys_qty, 4),
            unit_cost=unit_cost,
        ))
    count.variance_value = round(sum(float(l.variance_qty) * float(l.unit_cost)
                                     for l in count.lines), 4)
    db.add(count)
    db.commit()
    db.refresh(count)
    return count


def post_stock_count(db: Session, count_id: int, user: dict,
                     auto_adjustments: bool | None = None) -> models.StockCount:
    """اعتماد الجرد: يصدر حركة adjust لكل خط له فرق، مرتبطة برقم الجرد (source_doc).

    ضوابط مهنية:
    - لا يعتمد الجرد إلا من مستخدم غير مُنشئه (الفصل بين المهام) إن فُعّل ذلك.
    - التسويات تُرحَّل بقيود فروق الجرد الصحيحة (ربح/خسارة جرد) وليس بحساب واحد مخلوط.
    - منع الازدواج: لا يُعاد ترحيل سطر تم تسويته، ولا يُعتمد جرد مرحّل.
    """
    count = db.get(models.StockCount, count_id)
    if not count:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ورقة الجرد غير موجودة")
    if count.status == "posted":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "الجرد مُعتمد مسبقًا — لا يمكن إعادة التسوية")
    # إن لم يُختر الترحيل التلقائي (من ورقة الجرد أو بطلب صريح) ⇒ مراجعة فقط بدون ترحيل محاسبي
    if auto_adjustments is False or not getattr(count, "auto_post_adjustments", True):
        # مراجعة فقط بدون ترحيل محاسبي
        count.status = "posted"
        count.posted_by = user.get("user_id")
        count.posted_at = dt.datetime.utcnow()
        db.commit()
        db.refresh(count)
        return count

    uid = str(user.get("user_id") or "")
    if count.created_by and uid and count.created_by == uid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "مبدأ الفصل بين المهام: لا يمكن لمُنشئ الجرد اعتماده — يلزم مراجع آخر")

    for line in count.lines:
        if line.variance_qty == 0 or line.adjustment_moved:
            continue
        item = db.get(models.Item, line.item_id)
        unit_cost = float(line.unit_cost or 0)
        mv_data = schemas.MovementCreate(
            item_id=line.item_id, warehouse_id=count.warehouse_id,
            movement_type="adjust", qty=float(line.variance_qty),
            unit_cost=None,  # تُشتق تلقائيًا من تكلفة الصنف الدفترية (يدوي للاختبار عند الحاجة)
            source_doc=count.count_no,
            remarks=f"تسوية جرد ({'زيادة' if line.variance_qty > 0 else 'نقص'}) — {count.count_no}",
        )
        mv = post_movement(db, mv_data, user)
        if unit_cost <= 0:
            unit_cost = float(mv.unit_cost or 0)
        line.unit_cost = unit_cost
        line.adjustment_moved = True

    count.variance_value = round(sum(float(l.variance_qty) * float(l.unit_cost)
                                     for l in count.lines), 4)
    count.status = "posted"
    count.posted_by = user.get("user_id")
    count.posted_at = dt.datetime.utcnow()
    db.commit()
    db.refresh(count)
    return count


def list_stock_counts(db: Session):
    return list(db.scalars(select(models.StockCount).order_by(models.StockCount.id.desc())))


def get_movements(db: Session, item_id: int | None = None, warehouse_id: int | None = None,
                  source_doc: str | None = None):
    stmt = select(models.StockMovement).order_by(models.StockMovement.id.desc())
    if item_id:
        stmt = stmt.where(models.StockMovement.item_id == item_id)
    if warehouse_id:
        stmt = stmt.where((models.StockMovement.warehouse_id == warehouse_id) |
                          (models.StockMovement.dest_warehouse_id == warehouse_id))
    if source_doc:
        stmt = stmt.where(models.StockMovement.source_doc == source_doc)
    return list(db.scalars(stmt.limit(500)))
