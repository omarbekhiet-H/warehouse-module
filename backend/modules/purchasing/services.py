# -*- coding: utf-8 -*-
"""قسم المشتريات — منطق الأعمال.

سلسلة التكامل (عبر طبقات الخدمات فقط):
    purchasing.services.post_purchase_invoice
        └─> inventory.services.post_movement  (إضافة للرصيد)
                └─> accounting.services.create_journal_entry (قيد المخزون)
        └─> accounting.services.create_journal_entry (قيد المورد/الضريبة)
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


def post_purchase_invoice(db: Session, data: schemas.PurchaseCreate, user: dict) -> models.PurchaseInvoice:
    supplier = db.get(models.Supplier, data.supplier_id)
    if not supplier:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "المورد غير موجود")

    # إذا كانت الفاتورة مرتبطة بأمر شراء ⇒ المخزون أُضيف عند الاستلام،
    # فلا نكرر حركة "in" هنا (منع ازدواج الرصيد والقيود).
    po = db.get(models.PurchaseOrder, data.po_id) if data.po_id else None
    if data.po_id and not po:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "أمر الشراء المرتبط غير موجود")
    already_received = bool(po)  # الاستلام عبر GRN يضيف المخزون مسبقًا

    subtotal = 0.0
    invoice = models.PurchaseInvoice(
        invoice_no="", supplier_id=supplier.id, warehouse_id=data.warehouse_id,
        po_id=data.po_id,
        discount=data.discount, tax_rate=data.tax_rate,
        remarks=data.remarks, created_by=user.get("user_id"), status="draft",
    )
    for line in data.lines:
        item = db.get(inv_models.Item, line.item_id)
        if not item:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"الصنف رقم {line.item_id} غير موجود")
        lt = round(line.qty * line.unit_cost, 4)
        subtotal += lt
        invoice.lines.append(models.PurchaseInvoiceLine(
            item_id=line.item_id, qty=line.qty, unit_cost=line.unit_cost, line_total=lt))

    seq = (db.scalar(select(func.count(models.PurchaseInvoice.id))) or 0) + 1
    invoice.invoice_no = f"PINV-{dt.date.today():%Y%m%d}-{seq:05d}"
    net = max(subtotal - data.discount, 0)
    invoice.subtotal = round(subtotal, 4)
    invoice.tax_amount = round(net * data.tax_rate / 100, 4)
    invoice.total = round(net + invoice.tax_amount, 4)

    db.add(invoice)
    db.flush()

    # 1) إضافة المخزون لكل سطر (فاتورة مباشرة بلا إذن استلام ⇒ تُسجَّل حركة وارد بتكلفة الفاتورة الفعلية)
    if not already_received:
        for line in invoice.lines:
            inv_services.post_movement(
                db,
                inv_schemas.MovementCreate(
                    item_id=line.item_id, warehouse_id=data.warehouse_id,
                    movement_type="in", qty=float(line.qty),
                    unit_cost=float(line.unit_cost),   # التكلفة الفعلية من الفاتورة (Actual Cost)
                    source_doc=invoice.invoice_no,
                    remarks=f"وارد فاتورة شراء {invoice.invoice_no}",
                ),
                user,
            )

    # 2) قيد الشراء وفق نظام الجرد المستمر — بدون ازدواج مع قيود الحركات:
    #    - إذا سبق وأن أُضيف المخزون عبر GRN/حركة in ⇒ لا نعيد ترحيل قيمة البضاعة هنا،
    #      بل نسدد حساب "مخزون تحت التسوية (GRNI)" مقابل المورد والضريبة.
    #    - إذا كانت الفاتورة مباشرة ⇒ حركات الـ in رحّلت (مدين مخزون / دائن GRNI)،
    #      فنُسجل هنا نفس التسوية: مدين GRNI + ضريبة مستلمة / دائن المورد بالإجمالي.
    if supplier.payable_account_id:
        grni_acct = _grni_account(db)
        input_tax_acct = _first_account(db, "1200") or _get_or_create_account(
            db, "1200", "ضريبة القيمة المضافة (مدخلات)", "asset")
        lines_ = [
            acc_schemas.JournalLineIn(account_id=grni_acct,
                                      debit=float(invoice.subtotal - invoice.discount), credit=0),
            acc_schemas.JournalLineIn(account_id=supplier.payable_account_id,
                                      debit=0, credit=float(invoice.total)),
        ]
        if float(invoice.tax_amount) > 0 and input_tax_acct:
            lines_.insert(1, acc_schemas.JournalLineIn(account_id=input_tax_acct,
                                                       debit=float(invoice.tax_amount), credit=0))
        acc_services.create_journal_entry(
            db,
            acc_schemas.JournalEntryCreate(
                date=dt.date.today(),
                description=f"مشتريات {invoice.invoice_no} — {supplier.name_ar}",
                lines=lines_,
            ),
            user, source_module="purchasing", source_ref=invoice.invoice_no,
        )

    # 3) إغلاق أمر الشراء بعد مطابقة الفاتورة مع أذون الاستلام (Three-Way Match):
    #    الكمية المفوترة يجب ألا تتجاوز المستلم، والأمر لا يُغلق إلا بعد استلام كامل.
    if po:
        billed_by_item: dict[int, float] = {}
        for line in invoice.lines:
            billed_by_item[line.item_id] = billed_by_item.get(line.item_id, 0) + float(line.qty)
        received_by_item: dict[int, float] = {}
        for r in db.scalars(select(models.GoodsReceipt).where(models.GoodsReceipt.po_id == po.id)):
            for rl in r.lines:
                received_by_item[rl.item_id] = received_by_item.get(rl.item_id, 0) + float(rl.qty)
        for item_id, billed in billed_by_item.items():
            recv = received_by_item.get(item_id, 0)
            if billed > recv + 0.0001:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    f"مخالفة المطابقة الثلاثية: الكميات المفوترة ({billed}) "
                                    f"تجاوزت المستلم فعليًا ({recv}) للصنف رقم {item_id}")
        if po.status in ("received", "partially_received", "approved"):
            fully = all(float(pl.received_qty) >= float(pl.qty) - 0.0001 for pl in po.lines)
            if fully and abs(float(po.total) - float(invoice.subtotal)) < max(0.01, float(po.total) * 0.05):
                po.status = "closed"

    invoice.status = "posted"
    db.commit()
    db.refresh(invoice)
    return invoice


# ---------- أوامر الشراء ----------
def create_purchase_order(db: Session, data: schemas.POCreate, user: dict) -> models.PurchaseOrder:
    supplier = db.get(models.Supplier, data.supplier_id)
    if not supplier:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "المورد غير موجود")
    seq = (db.scalar(select(func.count(models.PurchaseOrder.id))) or 0) + 1
    po = models.PurchaseOrder(
        po_no=f"PO-{dt.date.today():%Y%m%d}-{seq:05d}",
        supplier_id=supplier.id, warehouse_id=data.warehouse_id,
        expected_date=data.expected_date, remarks=data.remarks,
        created_by=user.get("user_id"), status="draft",
    )
    total = 0.0
    for line in data.lines:
        if not db.get(inv_models.Item, line.item_id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"الصنف رقم {line.item_id} غير موجود")
        total += round(line.qty * line.unit_cost, 4)
        po.lines.append(models.PurchaseOrderLine(
            item_id=line.item_id, qty=line.qty, unit_cost=line.unit_cost))
    po.total = round(total, 4)
    db.add(po)
    db.commit()
    db.refresh(po)
    return po


def approve_purchase_order(db: Session, po_id: int) -> models.PurchaseOrder:
    po = db.get(models.PurchaseOrder, po_id)
    if not po:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "أمر الشراء غير موجود")
    if po.status != "draft":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"لا يمكن اعتماد أمر بحالة {po.status}")
    po.status = "approved"
    db.commit()
    db.refresh(po)
    return po


def list_purchase_orders(db: Session):
    return list(db.scalars(select(models.PurchaseOrder).order_by(models.PurchaseOrder.id.desc())))


# ---------- أذون الاستلام ----------
def create_goods_receipt(db: Session, data: schemas.ReceiptCreate, user: dict) -> models.GoodsReceipt:
    """إذن استلام: يتحقق من الكميات المتبقية في أمر الشراء، يضيف المخزون، ويحدّث حالة الـ PO."""
    po = db.get(models.PurchaseOrder, data.po_id)
    if not po:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "أمر الشراء غير موجود")
    if po.status not in ("approved", "partially_received"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"أمر الشراء بحالة {po.status} — يجب اعتماده أولًا")
    wh = db.get(inv_models.Warehouse, data.warehouse_id)
    if not wh:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "المخزن غير موجود")

    seq = (db.scalar(select(func.count(models.GoodsReceipt.id))) or 0) + 1
    grn = models.GoodsReceipt(
        receipt_no=f"GRN-{dt.date.today():%Y%m%d}-{seq:05d}",
        po_id=po.id, warehouse_id=data.warehouse_id,
        remarks=data.remarks, created_by=user.get("user_id"), status="posted",
    )
    db.add(grn)
    db.flush()

    # منع الاستلام المزدوج لنفس سطر أمر الشراء داخل الإذن الواحد
    seen_po_lines: set[int] = set()
    fully_received = True
    for line in data.lines:
        if line.po_line_id in seen_po_lines:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"سطر أمر الشراء رقم {line.po_line_id} مكرر في إذن الاستلام")
        seen_po_lines.add(line.po_line_id)
        po_line = db.get(models.PurchaseOrderLine, line.po_line_id)
        if not po_line or po_line.po_id != po.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"سطر أمر الشراء رقم {line.po_line_id} غير تابع لهذا الأمر")
        remaining = float(po_line.qty) - float(po_line.received_qty)
        if line.qty > remaining:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"الكمية المستلمة ({line.qty}) تتجاوز المتبقي في أمر الشراء ({remaining})")
        unit_cost = float(line.unit_cost) if line.unit_cost is not None else float(po_line.unit_cost)
        grn.lines.append(models.GoodsReceiptLine(
            po_line_id=po_line.id, item_id=po_line.item_id,
            qty=line.qty, unit_cost=unit_cost))
        po_line.received_qty = float(po_line.received_qty) + line.qty
        if float(po_line.received_qty) < float(po_line.qty):
            fully_received = False

        # حركة مخزون واردة مرتبطة برقم إذن الاستلام — تُرحَّل محاسبيًا
        # (مدين مخزون / دائن GRNI) بحركة "in" واحدة فقط لكل سطر.
        inv_services.post_movement(
            db,
            inv_schemas.MovementCreate(
                item_id=po_line.item_id, warehouse_id=data.warehouse_id,
                movement_type="in", qty=line.qty, unit_cost=unit_cost,
                source_doc=grn.receipt_no,
                remarks=f"وارد إذن استلام {grn.receipt_no} على أمر {po.po_no}",
            ),
            user,
        )

    # ربط الفاتورة الضريبية للمورد بإذن الاستلام إن أُرسل رقمها (تحديث حالة PO)
    if data.supplier_invoice_ref:
        grn.supplier_invoice_ref = data.supplier_invoice_ref

    po.status = "received" if fully_received else "partially_received"
    db.commit()
    db.refresh(grn)
    return grn


def list_goods_receipts(db: Session):
    return list(db.scalars(select(models.GoodsReceipt).order_by(models.GoodsReceipt.id.desc())))


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
