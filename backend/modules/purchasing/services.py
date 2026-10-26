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

    subtotal = 0.0
    invoice = models.PurchaseInvoice(
        invoice_no="", supplier_id=supplier.id,
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
    invoice.invoice_no = f"PUR-{dt.date.today():%Y%m%d}-{seq:05d}"
    net = max(subtotal - data.discount, 0)
    invoice.subtotal = round(subtotal, 4)
    invoice.tax_amount = round(net * data.tax_rate / 100, 4)
    invoice.total = round(net + invoice.tax_amount, 4)

    db.add(invoice)
    db.flush()

    # 1) إضافة المخزون لكل سطر (تولّد قيد المخزون المحاسبي تلقائيًا)
    for line in invoice.lines:
        inv_services.post_movement(
            db,
            inv_schemas.MovementCreate(
                item_id=line.item_id, warehouse_id=data.warehouse_id,
                movement_type="in", qty=float(line.qty),
                unit_cost=float(line.unit_cost),
                remarks=f"وارد فاتورة شراء {invoice.invoice_no}",
            ),
            user,
        )

    # 2) قيد المورد: مدين المخزون/المصروف + الضريبة، دائن المورد بالإجمالي
    if supplier.payable_account_id:
        stock_acct = _first_inventory_account(db)
        input_tax_acct = _first_account(db, "1200")
        lines_ = [
            acc_schemas.JournalLineIn(account_id=stock_acct,
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

    invoice.status = "posted"
    db.commit()
    db.refresh(invoice)
    return invoice


def register_payment(db: Session, invoice_id: int, amount: float) -> models.PurchaseInvoice:
    inv = db.get(models.PurchaseInvoice, invoice_id)
    if not inv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "فاتورة الشراء غير موجودة")
    if amount > float(inv.total) - float(inv.paid_amount):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "المبلغ أكبر من المتبقي على الفاتورة")
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


def _first_inventory_account(db: Session) -> int:
    from backend.modules.accounting import models as acc_models
    acct = db.scalar(select(acc_models.Account).where(acc_models.Account.code == "1300")) \
        or db.scalar(select(acc_models.Account).where(acc_models.Account.account_type == "asset").limit(1))
    if not acct:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "لا يوجد حساب مخزون معرّف في دليل الحسابات")
    return acct.id
