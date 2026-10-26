# -*- coding: utf-8 -*-
"""قسم المشتريات — منطق الأعمال لدورة المشتريات المتكاملة (الأصول المهنية).

الدورة المستندية الكاملة:
    طلب الاحتياجات (MR)  [مسودة/معتمد/مرفوض — بلا أثر مخزني أو محاسبي]
      └─> تحويل تلقائي لأمر شراء (PO) [التزام تعاقدي — بلا قيد يومية]
            └─> إذن توريد وارد من المورد (DN) [ترقّب ومطابقة كميات قبل الفحص]
                  └─> إذن استلام مخزني (GRN) [فحص نوعي: مقبول/مرفود]
                        └─> حركة إضافة للمخزن (in) ⇒ مدين المخزون / دائن GRNI (2100)
                              └─> فاتورة الشراء (Three-Way Match: PO × GRN × Invoice)
                                    ⇒ مدين GRNI + ضريبة مدخلات / دائن المورد بالإجمالي
                                    ⇒ توزيع الشحن والجمارك على تكلفة الأصناف (IAS 2)

سلسلة التكامل تتم عبر طبقات الخدمات فقط:
    purchasing.services → inventory.services → accounting.services
"""
from __future__ import annotations

import datetime as dt

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.modules.accounting import schemas as acc_schemas
from backend.modules.accounting import services as acc_services
from backend.modules.inventory import models as inv_models
from backend.modules.inventory import schemas as inv_schemas
from backend.modules.inventory import services as inv_services
from backend.modules.purchasing import models, schemas


# ============================ 1) طلب الاحتياجات ============================
def create_material_request(db: Session, data: schemas.MRCreate, user: dict) -> models.MaterialRequest:
    """طلب احتياجات داخلي — لا يؤثر على المخزون أو الحسابات (مستند مراجعة فقط)."""
    seq = (db.scalar(select(func.count(models.MaterialRequest.id))) or 0) + 1
    mr = models.MaterialRequest(
        mr_no=f"MR-{dt.date.today():%Y%m%d}-{seq:05d}",
        requested_by=data.requested_by, department_id=data.department_id,
        department_name=data.department_name, warehouse_id=data.warehouse_id,
        reason=data.reason, remarks=data.remarks,
        created_by=user.get("user_id"), status="draft",
    )
    for line in data.lines:
        if not db.get(inv_models.Item, line.item_id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"الصنف رقم {line.item_id} غير موجود")
        mr.lines.append(models.MaterialRequestLine(item_id=line.item_id, qty=line.qty,
                                                   remarks=line.remarks))
    db.add(mr)
    db.commit()
    db.refresh(mr)
    return mr


def _mr_decide(db: Session, mr_id: int, approve: bool, user: dict,
               reason: str | None) -> models.MaterialRequest:
    mr = db.get(models.MaterialRequest, mr_id)
    if not mr:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "طلب الاحتياجات غير موجود")
    if mr.status != "draft":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"لا يمكن اتخاذ قرار لطلب بحالة {mr.status}")
    # الفصل بين المهام: منشئ الطلب لا يعتمد طلبه بنفسه إن وُجد مستخدم موثوق مختلف
    creator = mr.created_by
    approver = user.get("user_id")
    if creator and approver and str(creator) == str(approver) and user.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "لا يجوز لمُنشئ الطلب اعتماد طلبه (مبدأ الفصل بين المهام)")
    mr.status = "approved" if approve else "rejected"
    mr.approved_by = approver
    mr.approved_at = dt.datetime.utcnow()
    if reason:
        mr.remarks = (f"{mr.remarks or ''}\nقرار: {reason}").strip()
    db.commit()
    db.refresh(mr)
    return mr


def approve_material_request(db: Session, mr_id: int, user: dict,
                             reason: str | None = None) -> models.MaterialRequest:
    return _mr_decide(db, mr_id, True, user, reason)


def reject_material_request(db: Session, mr_id: int, user: dict,
                            reason: str | None = None) -> models.MaterialRequest:
    return _mr_decide(db, mr_id, False, user, reason)


def list_material_requests(db: Session, status_filter: str | None = None):
    stmt = select(models.MaterialRequest).order_by(models.MaterialRequest.id.desc())
    if status_filter:
        stmt = stmt.where(models.MaterialRequest.status == status_filter)
    return list(db.scalars(stmt))


# ============================ 2) أوامر الشراء ============================
def create_purchase_order(db: Session, data: schemas.POCreate, user: dict) -> models.PurchaseOrder:
    supplier = db.get(models.Supplier, data.supplier_id)
    if not supplier:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "المورد غير موجود")

    # التحقق من مصدر الطلب المعتمد وسحب الكميات دون تجاوز المتبقي غير المحوَّل
    mr = db.get(models.MaterialRequest, data.mr_id) if data.mr_id else None
    if data.mr_id and not mr:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "طلب الاحتياجات المرتبط غير موجود")
    if mr and mr.status != "approved":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "لا يُصدر أمر شراء إلا من طلب احتياجات معتمد")
    mr_lines_by_item: dict[int, float] = {}
    if mr:
        for ml in mr.lines:
            remaining = float(ml.qty) - float(ml.converted_qty)
            mr_lines_by_item[ml.item_id] = mr_lines_by_item.get(ml.item_id, 0) + remaining

    seq = (db.scalar(select(func.count(models.PurchaseOrder.id))) or 0) + 1
    po = models.PurchaseOrder(
        po_no=f"PO-{dt.date.today():%Y%m%d}-{seq:05d}",
        supplier_id=supplier.id, warehouse_id=data.warehouse_id,
        mr_id=mr.id if mr else None, payment_terms=data.payment_terms,
        expected_date=data.expected_date, remarks=data.remarks,
        created_by=user.get("user_id"), status="draft",
    )
    total = 0.0
    ordered_by_item: dict[int, float] = {}
    for line in data.lines:
        item = db.get(inv_models.Item, line.item_id)
        if not item:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"الصنف رقم {line.item_id} غير موجود")
        ordered_by_item[line.item_id] = ordered_by_item.get(line.item_id, 0) + line.qty
        if mr and line.mr_line_id is None and ordered_by_item[line.item_id] > mr_lines_by_item.get(line.item_id, 0) + 0.0001:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"الكمية المطلوبة للصنف {item.name_ar} تتجاوز المتبقي في طلب الاحتياجات "
                                f"({mr_lines_by_item.get(line.item_id, 0)})")
        unit_cost = float(line.unit_cost) or float(item.purchase_price or item.standard_cost or 0)
        total += round(line.qty * unit_cost, 4)
        po.lines.append(models.PurchaseOrderLine(
            item_id=line.item_id, qty=line.qty, unit_cost=unit_cost,
            mr_line_id=line.mr_line_id))
    po.total = round(total, 4)
    db.add(po)
    db.flush()

    # تحديث كميات التحويل في أسطر طلب الاحتياجات (تتبع المصدر ومنع التكرار)
    if mr:
        for pl in po.lines:
            if pl.mr_line_id:
                ml = db.get(models.MaterialRequestLine, pl.mr_line_id)
                if not ml or ml.mr_id != mr.id:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                        f"سطر طلب الاحتياجات {pl.mr_line_id} غير تابع للطلب المرتبط")
                ml.converted_qty = float(ml.converted_qty) + float(pl.qty)
            else:
                for ml in mr.lines:
                    if ml.item_id == pl.item_id and float(ml.converted_qty) < float(ml.qty):
                        take = min(float(pl.qty), float(ml.qty) - float(ml.converted_qty))
                        ml.converted_qty = float(ml.converted_qty) + take
                        break
        if all(float(ml.converted_qty) >= float(ml.qty) - 0.0001 for ml in mr.lines):
            mr.status = "closed"

    db.commit()
    db.refresh(po)
    return po


def convert_mr_to_po(db: Session, mr_id: int, data: schemas.POFromMRIn,
                     user: dict) -> models.PurchaseOrder:
    """تحويل كامل لطلب احتياجات معتمد إلى أمر شراء — سحب تلقائي للكميات (بدون إدخال يدوي)."""
    mr = db.get(models.MaterialRequest, mr_id)
    if not mr:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "طلب الاحتياجات غير موجود")
    if mr.status not in ("approved",):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"لا يُحوَّل طلب بحالة {mr.status} — يجب اعتماده أولًا")
    lines = []
    for ml in mr.lines:
        remaining = float(ml.qty) - float(ml.converted_qty)
        if remaining <= 0.0001:
            continue
        price = 0.0
        if data.unit_costs and ml.id in data.unit_costs:
            price = float(data.unit_costs[ml.id])
        if not price:
            item = db.get(inv_models.Item, ml.item_id)
            price = float((item.purchase_price if item else 0) or (item.standard_cost if item else 0) or 0)
        lines.append(schemas.POLineIn(item_id=ml.item_id, qty=remaining,
                                      unit_cost=price, mr_line_id=ml.id))
    if not lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "لا توجد كميات متبقية غير محوَّلة في هذا الطلب")
    po_data = schemas.POCreate(
        supplier_id=data.supplier_id,
        warehouse_id=data.warehouse_id or mr.warehouse_id,
        mr_id=mr.id, expected_date=data.expected_date,
        payment_terms=data.payment_terms,
        remarks=f"محوَّل تلقائيًا من طلب الاحتياجات {mr.mr_no}",
        lines=lines,
    )
    return create_purchase_order(db, po_data, user)


def approve_purchase_order(db: Session, po_id: int) -> models.PurchaseOrder:
    po = db.get(models.PurchaseOrder, po_id)
    if not po:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "أمر الشراء غير موجود")
    if po.status != "draft":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"لا يمكن اعتماد أمر بحالة {po.status}")
    # الالتزام التعاقدي لا يُرحَّل محاسبيًا (لا تبادل فعلي لبضائع/أموال)
    po.status = "approved"
    db.commit()
    db.refresh(po)
    return po


def send_purchase_order(db: Session, po_id: int) -> models.PurchaseOrder:
    po = db.get(models.PurchaseOrder, po_id)
    if not po:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "أمر الشراء غير موجود")
    if po.status != "approved":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "يجب اعتماد الأمر قبل إرساله للمورد")
    po.status = "sent"
    db.commit()
    db.refresh(po)
    return po


def close_purchase_order(db: Session, po_id: int) -> models.PurchaseOrder:
    po = db.get(models.PurchaseOrder, po_id)
    if not po:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "أمر الشراء غير موجود")
    if po.status in ("closed", "cancelled"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"الأمر بالفعل {po.status}")
    po.status = "closed"
    db.commit()
    db.refresh(po)
    return po


def list_purchase_orders(db: Session):
    return list(db.scalars(select(models.PurchaseOrder).order_by(models.PurchaseOrder.id.desc())))


# ============================ 3) إذن التوريد الوارد (DN) ============================
def create_delivery_note(db: Session, data: schemas.DNCreate, user: dict) -> models.DeliveryNote:
    """إذن التوريد من المورد: تسجيل الكميات المعلن عنها والتحقق أنها لا تتجاوز المتبقي في الـ PO."""
    po = db.get(models.PurchaseOrder, data.po_id)
    if not po:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "أمر الشراء غير موجود")
    if po.status not in ("approved", "sent", "partially_received"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"أمر الشراء بحالة {po.status} — يجب اعتماده وإرساله قبل التوريد")
    seq = (db.scalar(select(func.count(models.DeliveryNote.id))) or 0) + 1
    dn = models.DeliveryNote(
        dn_no=f"DN-{dt.date.today():%Y%m%d}-{seq:05d}",
        supplier_dn_no=data.supplier_dn_no, po_id=po.id, vehicle=data.vehicle,
        remarks=data.remarks, created_by=user.get("user_id"), status="pending",
    )
    seen: set[int] = set()
    declared_total = 0.0
    for line in data.lines:
        if line.po_line_id in seen:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"سطر أمر الشراء {line.po_line_id} مكرر في إذن التوريد")
        seen.add(line.po_line_id)
        po_line = db.get(models.PurchaseOrderLine, line.po_line_id)
        if not po_line or po_line.po_id != po.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"سطر أمر الشراء {line.po_line_id} غير تابع لهذا الأمر")
        outstanding = float(po_line.qty) - float(po_line.received_qty)
        if line.qty_declared > outstanding + 0.0001:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"الكمية المعلن توريدها ({line.qty_declared}) تتجاوز المتبقي بأمر الشراء ({outstanding})")
        dn.lines.append(models.DeliveryNoteLine(po_line_id=po_line.id, item_id=po_line.item_id,
                                                qty_declared=line.qty_declared))
        declared_total += line.qty_declared
    dn.declared_qty = round(declared_total, 4)
    db.add(dn)
    db.commit()
    db.refresh(dn)
    return dn


def list_delivery_notes(db: Session):
    return list(db.scalars(select(models.DeliveryNote).order_by(models.DeliveryNote.id.desc())))


# ============================ 4+5) إذن الاستلام ⇒ الإضافة للمخزن ============================
def create_goods_receipt(db: Session, data: schemas.ReceiptCreate, user: dict) -> models.GoodsReceipt:
    """إذن استلام مخزني بعد الفحص النوعي: يسجّل المقبول/المرفود، يضيف المخزون بحركة (in)
    ترحّل تلقائيًا (مدين المخزون / دائن GRNI)، ويحدّث حالة أمر الشراء وإذن التوريد."""
    po = db.get(models.PurchaseOrder, data.po_id)
    if not po:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "أمر الشراء غير موجود")
    if po.status not in ("approved", "sent", "partially_received"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"أمر الشراء بحالة {po.status} — يجب اعتماده أولًا")
    wh = db.get(inv_models.Warehouse, data.warehouse_id)
    if not wh:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "المخزن غير موجود")

    dn = db.get(models.DeliveryNote, data.dn_id) if data.dn_id else None
    if data.dn_id and (not dn or dn.po_id != po.id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "إذن التوريد المرتبط غير موجود أو غير تابع للأمر")

    seq = (db.scalar(select(func.count(models.GoodsReceipt.id))) or 0) + 1
    grn = models.GoodsReceipt(
        receipt_no=f"GRN-{dt.date.today():%Y%m%d}-{seq:05d}",
        po_id=po.id, dn_id=dn.id if dn else None, warehouse_id=data.warehouse_id,
        inspector=data.inspector or user.get("username"),
        remarks=data.remarks, created_by=user.get("user_id"), status="posted",
    )
    db.add(grn)
    db.flush()

    seen_po_lines: set[int] = set()
    fully_received = True
    accepted_total = 0.0
    for line in data.lines:
        if line.po_line_id in seen_po_lines:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"سطر أمر الشراء رقم {line.po_line_id} مكرر في إذن الاستلام")
        seen_po_lines.add(line.po_line_id)
        po_line = db.get(models.PurchaseOrderLine, line.po_line_id)
        if not po_line or po_line.po_id != po.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"سطر أمر الشراء رقم {line.po_line_id} غير تابع لهذا الأمر")
        accepted = float(line.qty_accepted)
        rejected = float(line.qty_rejected)
        if accepted + rejected <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "يجب تسجيل كمية مقبولة أو مرفوضة على الأقل لسطر الاستلام")
        remaining = float(po_line.qty) - float(po_line.received_qty)
        if accepted > remaining + 0.0001:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"الكمية المستلمة ({accepted}) تتجاوز المتبقي في أمر الشراء ({remaining:.4f})")
        # مطابقة أمام الكميات المعلنة في إذن التوريد إن وُجد
        if dn:
            dn_line = next((dl for dl in dn.lines if dl.po_line_id == po_line.id), None)
            if dn_line and accepted + rejected > float(dn_line.qty_declared) + 0.0001:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    f"المقبول + المرفود ({accepted + rejected}) يتجاوز الكمية المعلن توريدها "
                                    f"في إذن التوريد ({float(dn_line.qty_declared)})")
            if dn_line:
                dn_line.qty_accepted = float(dn_line.qty_accepted) + accepted
                dn_line.qty_rejected = float(dn_line.qty_rejected) + rejected

        unit_cost = float(line.unit_cost) if line.unit_cost is not None else float(po_line.unit_cost)
        grn.lines.append(models.GoodsReceiptLine(
            po_line_id=po_line.id, item_id=po_line.item_id,
            qty=accepted, qty_rejected=rejected, unit_cost=unit_cost))
        po_line.received_qty = float(po_line.received_qty) + accepted
        accepted_total += accepted
        if float(po_line.received_qty) < float(po_line.qty):
            fully_received = False

        if accepted > 0:
            # حركة مخزون واردة مرتبطة برقم إذن الاستلام — تُرحَّل محاسبيًا
            # (مدين مخزون / دائن بضاعة مستلمة ولم تُفوتر GRNI) — المرحلة 5 من الدورة.
            inv_services.post_movement(
                db,
                inv_schemas.MovementCreate(
                    item_id=po_line.item_id, warehouse_id=data.warehouse_id,
                    movement_type="in", qty=accepted, unit_cost=unit_cost,
                    source_doc=grn.receipt_no,
                    remarks=f"وارد إذن استلام {grn.receipt_no} على أمر {po.po_no}"
                            + (f" — مرفود {rejected} عند الفحص" if rejected else ""),
                ),
                user,
            )

    if dn:
        all_done = all(float(dl.qty_accepted) + float(dl.qty_rejected) >= float(dl.qty_declared) - 0.0001
                       for dl in dn.lines)
        dn.status = "received" if all_done else "partially_received"

    if data.supplier_invoice_ref:
        grn.supplier_invoice_ref = data.supplier_invoice_ref

    po.status = "received" if fully_received else "partially_received"
    db.commit()
    db.refresh(grn)
    return grn


def list_goods_receipts(db: Session):
    return list(db.scalars(select(models.GoodsReceipt).order_by(models.GoodsReceipt.id.desc())))


# ============================ 6+7) الفوترة والتأثير النهائي على الحسابات ============================
def post_purchase_invoice(db: Session, data: schemas.PurchaseCreate, user: dict) -> models.PurchaseInvoice:
    """فاتورة الشراء — نهاية الدورة المستندية وبداية الأثر المحاسبي النهائي.

    القيد الناتج (بعد أن تكون حركات الاستلام قد رحّلت مدين المخزون/دائن GRNI):
        من مذكورين:
            ح/ بضاعة مستلمة ولم تُفوتر GRNI (2100)  [صافي قيمة الأصناف بعد الخصم]
            ح/ مصاريف شحن وجمارك (5920)             [إن لم تُوزَّع على الأصناف]
            ح/ ضريبة القيمة المضافة مدخلات (1200)
        إلى ح/ الموردين (الإجمالي شامل الضريبة)
    المطابقة الثلاثية: الكميات المفوترة ≤ المستلم فعليًا ≤ المطلوب بأمر الشراء.
    """
    supplier = db.get(models.Supplier, data.supplier_id)
    if not supplier:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "المورد غير موجود")

    po = db.get(models.PurchaseOrder, data.po_id) if data.po_id else None
    if data.po_id and not po:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "أمر الشراء المرتبط غير موجود")
    grn = db.get(models.GoodsReceipt, data.grn_id) if data.grn_id else None
    if data.grn_id and (not grn or grn.status != "posted"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "إذن الاستلام المرتبط غير موجود أو ملغي")
    if grn and po and grn.po_id != po.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "إذن الاستلام غير تابع لأمر الشراء المرتبط")
    if grn and not po:
        po = grn.order  # اشتقاق الأمر من الإذن لضمان صحة المطابقة
    already_received = bool(po) or bool(grn)  # الاستلام عبر GRN أضاف المخزون وقيّد GRNI مسبقًا

    invoice = models.PurchaseInvoice(
        invoice_no="", supplier_id=supplier.id, warehouse_id=data.warehouse_id,
        po_id=po.id if po else None, grn_id=grn.id if grn else None,
        discount=data.discount, freight_charges=data.freight_charges,
        customs_charges=data.customs_charges, tax_rate=data.tax_rate,
        remarks=data.remarks, created_by=user.get("user_id"), status="draft",
    )

    # ---- سحب أسطر الفاتورة تلقائيًا من إذن الاستلام عند عدم إرسالها يدويًا ----
    src_lines: list[tuple[int, float, float]] = []  # (item_id, qty, unit_cost)
    if grn:
        src_lines = [(l.item_id, float(l.qty), float(l.unit_cost)) for l in grn.lines if float(l.qty) > 0]
    elif po:
        src_lines = [(l.item_id, float(l.received_qty), float(l.unit_cost))
                     for l in po.lines if float(l.received_qty) > 0]
    request_lines = [(l.item_id, float(l.qty), float(l.unit_cost)) for l in data.lines]
    effective = request_lines or src_lines
    if not effective:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "لا توجد كميات مستلمة للفوترة — أنشئ إذن استلام أولًا أو أرسل أسطر الفاتورة")

    subtotal = 0.0
    for item_id, qty, unit_cost in effective:
        item = db.get(inv_models.Item, item_id)
        if not item:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"الصنف رقم {item_id} غير موجود")
        lt = round(qty * unit_cost, 4)
        subtotal += lt
        invoice.lines.append(models.PurchaseInvoiceLine(
            item_id=item_id, qty=qty, unit_cost=unit_cost, line_total=lt))

    seq = (db.scalar(select(func.count(models.PurchaseInvoice.id))) or 0) + 1
    invoice.invoice_no = f"PINV-{dt.date.today():%Y%m%d}-{seq:05d}"
    net = max(subtotal - data.discount, 0)
    invoice.subtotal = round(subtotal, 4)
    invoice.tax_amount = round(net * data.tax_rate / 100, 4)
    invoice.total = round(net + invoice.tax_amount, 4)

    db.add(invoice)
    db.flush()

    # ربط إذن الاستلام بالفاتورة (عكسي) لتتبع الدورة كاملة
    if grn:
        grn.invoice_id = invoice.id

    # ---- فاتورة مباشرة بلا PO/GRN: البضاعة تدخل المخزون الآن بحركة (in) بتكلفة الفعلية ----
    if not already_received:
        if not data.warehouse_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "الفاتورة المباشرة تتطلب تحديد مخزن الإضافة")
        for line in invoice.lines:
            inv_services.post_movement(
                db,
                inv_schemas.MovementCreate(
                    item_id=line.item_id, warehouse_id=data.warehouse_id,
                    movement_type="in", qty=float(line.qty),
                    unit_cost=float(line.unit_cost),   # التكلفة الفعلية من الفاتورة (Actual Cost)
                    source_doc=invoice.invoice_no,
                    remarks=f"وارد فاتورة شراء مباشرة {invoice.invoice_no}",
                ),
                user,
            )

    # ---- 7) التأثير النهائي على الحسابات العامة ----
    if supplier.payable_account_id:
        grni_acct = _grni_account(db)
        input_tax_acct = _first_account(db, "1200") or _get_or_create_account(
            db, "1200", "ضريبة القيمة المضافة (مدخلات)", "asset")
        # الشحن والجمارك: حسب IAS 2 تدخل في تكلفة المخزون؛ نوزّعها على الأصناف
        # بحركة تسوية موجبة، وإن تعذّر التوزيع ترحل كمصروف مباشر.
        addl = round(float(data.freight_charges) + float(data.customs_charges), 4)
        lines_: list[acc_schemas.JournalLineIn] = [
            acc_schemas.JournalLineIn(account_id=grni_acct,
                                      debit=float(invoice.subtotal - invoice.discount), credit=0),
            acc_schemas.JournalLineIn(account_id=supplier.payable_account_id,
                                      debit=0, credit=float(invoice.total)),
        ]
        if float(invoice.tax_amount) > 0 and input_tax_acct:
            lines_.insert(1, acc_schemas.JournalLineIn(account_id=input_tax_acct,
                                                       debit=float(invoice.tax_amount), credit=0))
        if addl > 0:
            if already_received:
                # إضافات التكلفة (شحن/جمارك) تُضاف لرصيد المخزون محاسبيًا وفق IAS 2،
                # وتوزَّ قيمتها على الأصناف في ملاحظات القيد (بروبورتا قيمة كل صنف).
                inv_acct = _default_inventory_account(db)
                lines_.append(acc_schemas.JournalLineIn(account_id=inv_acct, debit=addl, credit=0))
                lines_.append(acc_schemas.JournalLineIn(account_id=grni_acct, debit=0, credit=addl))
            else:
                # فاتورة مباشرة بلا استلام سابق ⇒ الشحن/الجمارك كمصروف مستقل مقابل المورد
                expense_acct = _get_or_create_account(db, "5920", "مصاريف شحن وجمارك", "expense")
                lines_.append(acc_schemas.JournalLineIn(account_id=expense_acct, debit=addl, credit=0))
                for ln in lines_:
                    if ln.account_id == supplier.payable_account_id:
                        ln.credit = float(ln.credit) + addl
                        break
                invoice.total = round(float(invoice.total) + addl, 4)

        acc_services.create_journal_entry(
            db,
            acc_schemas.JournalEntryCreate(
                date=dt.date.today(),
                description=f"مشتريات {invoice.invoice_no} — {supplier.name_ar}",
                lines=lines_,
            ),
            user, source_module="purchasing", source_ref=invoice.invoice_no,
        )

    # ---- المطابقة الثلاثية (PO × GRN × Invoice) وتحديث billed_qty ----
    if po:
        billed_by_item: dict[int, float] = {}
        for line in invoice.lines:
            billed_by_item[line.item_id] = billed_by_item.get(line.item_id, 0) + float(line.qty)
        received_by_item: dict[int, float] = {}
        for r in db.scalars(select(models.GoodsReceipt).where(models.GoodsReceipt.po_id == po.id,
                                                              models.GoodsReceipt.status == "posted")):
            for rl in r.lines:
                received_by_item[rl.item_id] = received_by_item.get(rl.item_id, 0) + float(rl.qty)
        for item_id, billed in billed_by_item.items():
            recv = received_by_item.get(item_id, 0)
            if billed > recv + 0.0001:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    f"مخالفة المطابقة الثلاثية: الكميات المفوترة ({billed}) "
                                    f"تجاوزت المستلم فعليًا ({recv}) للصنف رقم {item_id}")
        # تحديث الكميات المفوترة على أسطر الأمر
        for pl in po.lines:
            add = sum(float(l.qty) for l in invoice.lines if l.item_id == pl.item_id)
            if add:
                pl.billed_qty = float(pl.billed_qty) + add
        if po.status in ("received", "partially_received", "approved", "sent"):
            fully = all(float(pl.received_qty) >= float(pl.qty) - 0.0001 for pl in po.lines)
            billed_fully = all(float(pl.billed_qty) >= float(pl.received_qty) - 0.0001 for pl in po.lines)
            tol = max(0.01, float(po.total) * 0.05)
            if fully and billed_fully and abs(float(po.total) - float(invoice.subtotal)) <= tol:
                po.status = "closed"

    invoice.status = "posted"
    db.commit()
    db.refresh(invoice)
    return invoice


def list_suppliers(db: Session, search: str | None = None):
    stmt = select(models.Supplier).where(models.Supplier.is_active.is_(True))
    if search:
        stmt = stmt.where(models.Supplier.name_ar.like(f"%{search}%"))
    return list(db.scalars(stmt.order_by(models.Supplier.name_ar)))


def create_supplier(db: Session, data: schemas.SupplierCreate) -> models.Supplier:
    obj = models.Supplier(**data.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def register_payment(db: Session, invoice_id: int, amount: float,
                     user: dict | None = None) -> models.PurchaseInvoice:
    """تسجيل دفعة للمورد مع قيدها المحاسبي (مدين المورد / دائن النقدية).

    مسار موصى به للنظام الكامل: الدفعات الرسمية تمر عبر سندات الخزينة (/treasury/vouchers)
    التي تربط طريقة السداد وتوزع على الفواتير FIFO؛ هذه النقطة تبقى للمسار المبسّط.
    """
    inv = db.get(models.PurchaseInvoice, invoice_id)
    if not inv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "فاتورة الشراء غير موجودة")
    if inv.status == "draft":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "لا تُسدد دفعة على فاتورة غير مرحّلة")
    remaining = float(inv.total) - float(inv.paid_amount)
    if amount > remaining:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "المبلغ أكبر من المتبقي على الفاتورة")

    supplier = db.get(models.Supplier, inv.supplier_id)
    bank_acct = _first_account(db, "1100") or _get_or_create_account(
        db, "1100", "البنك / النقدية", "asset")
    if supplier and supplier.payable_account_id:
        acc_services.create_journal_entry(
            db,
            acc_schemas.JournalEntryCreate(
                date=dt.date.today(),
                description=f"دفعة للمورد {supplier.name_ar} على فاتورة {inv.invoice_no}",
                lines=[
                    acc_schemas.JournalLineIn(account_id=supplier.payable_account_id,
                                              debit=amount, credit=0),
                    acc_schemas.JournalLineIn(account_id=bank_acct,
                                              debit=0, credit=amount),
                ],
            ),
            user or {"user_id": "system"}, source_module="purchasing",
            source_ref=inv.invoice_no,
        )

    inv.paid_amount = float(inv.paid_amount) + amount
    if float(inv.paid_amount) >= float(inv.total):
        inv.status = "paid"
    db.commit()
    db.refresh(inv)
    return inv


def list_invoices(db: Session):
    return list(db.scalars(select(models.PurchaseInvoice).order_by(models.PurchaseInvoice.id.desc())))


def _first_account(db: Session, code: str) -> int | None:
    from backend.modules.accounting import models as acc_models
    acct = db.scalar(select(acc_models.Account).where(acc_models.Account.code == code))
    return acct.id if acct else None


def _get_or_create_account(db: Session, code: str, name_ar: str, account_type: str) -> int:
    """يضمن وجود حساب التكامل في دليل الحسابات (عبر خدمات المحاسبة فقط)."""
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
    """حساب مقابل مرحلي: بضاعة مستلمة ولم تُفوتر بعد — يُسدد عند قيد الفاتورة."""
    return _get_or_create_account(db, "2100", "مخزون تحت التسوية (GRNI)", "liability")


def _default_inventory_account(db: Session) -> int:
    return _get_or_create_account(db, "1300", "المخزون", "asset")
