# -*- coding: utf-8 -*-
"""قسم المبيعات — منطق الأعمال.

سلسلة التكامل بين الأقسام (كلها عبر طبقات الخدمات، لا استيراد جداول):
    sales.services.post_invoice
        └─> inventory.services.post_movement   (خصم الرصيد)
                └─> accounting.services.create_journal_entry (قيد المخزون)
        └─> accounting.services.create_journal_entry (قيد الإيراد/العميل)
"""
from __future__ import annotations

import datetime as dt

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.modules.accounting import schemas as acc_schemas
from backend.modules.accounting import services as acc_services
from backend.modules.inventory import schemas as inv_schemas
from backend.modules.inventory import services as inv_services
from backend.modules.sales import models, schemas


# ---------- العملاء ----------
def list_customers(db: Session, search: str | None = None):
    stmt = select(models.Customer).where(models.Customer.is_active.is_(True))
    if search:
        stmt = stmt.where(models.Customer.name_ar.like(f"%{search}%"))
    return list(db.scalars(stmt.order_by(models.Customer.name_ar)))


def create_customer(db: Session, data: schemas.CustomerCreate) -> models.Customer:
    obj = models.Customer(**data.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


# ---------- الفواتير ----------
def post_invoice(db: Session, data: schemas.InvoiceCreate, user: dict) -> models.Invoice:
    customer = db.get(models.Customer, data.customer_id)
    if not customer:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "العميل غير موجود")

    subtotal = 0.0
    invoice = models.Invoice(
        invoice_no="",  # يُولَّد بعد معرفة التسلسل
        customer_id=customer.id,
        discount=data.discount,
        tax_rate=data.tax_rate,
        remarks=data.remarks,
        created_by=user.get("user_id"),
        status="draft",
    )
    for line in data.lines:
        item = _get_item(db, line.item_id)
        if not item:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"الصنف رقم {line.item_id} غير موجود")
        lt = round(line.qty * line.unit_price, 4)
        subtotal += lt
        invoice.lines.append(models.InvoiceLine(
            item_id=line.item_id, qty=line.qty, unit_price=line.unit_price, line_total=lt))

    seq = (db.scalar(select(func.count(models.Invoice.id))) or 0) + 1
    invoice.invoice_no = f"INV-SALE-{dt.date.today():%Y%m%d}-{seq:05d}"
    net = max(subtotal - data.discount, 0)
    invoice.subtotal = round(subtotal, 4)
    invoice.tax_amount = round(net * data.tax_rate / 100, 4)
    invoice.total = round(net + invoice.tax_amount, 4)

    db.add(invoice)
    db.flush()

    # 1) خصم المخزون لكل سطر (يستدعي بدوره قيد المخزون المحاسبي)
    for line in invoice.lines:
        inv_services.post_movement(
            db,
            inv_schemas.MovementCreate(
                item_id=line.item_id, warehouse_id=data.warehouse_id,
                movement_type="out", qty=float(line.qty),
                unit_cost=_item_cost(db, line.item_id),
                remarks=f"صادر فاتورة بيع {invoice.invoice_no}",
            ),
            user,
        )

    # 2) قيد الإيراد: مدين العميل بالإجمالي، دائن الإيراد الصافي + الضريبة
    if customer.receivable_account_id:
        revenue_acct = _revenue_account(db)
        tax_acct = _tax_account(db)
        lines_ = [
            acc_schemas.JournalLineIn(account_id=customer.receivable_account_id,
                                      debit=float(invoice.total), credit=0),
            acc_schemas.JournalLineIn(account_id=revenue_acct,
                                      debit=0, credit=float(invoice.subtotal - invoice.discount)),
        ]
        if float(invoice.tax_amount) > 0 and tax_acct:
            lines_.append(acc_schemas.JournalLineIn(account_id=tax_acct,
                                                    debit=0, credit=float(invoice.tax_amount)))
        acc_services.create_journal_entry(
            db,
            acc_schemas.JournalEntryCreate(
                date=dt.date.today(),
                description=f"إيراد فاتورة بيع {invoice.invoice_no} — {customer.name_ar}",
                lines=lines_,
            ),
            user, source_module="sales", source_ref=invoice.invoice_no,
        )

    invoice.status = "posted"
    db.commit()
    db.refresh(invoice)
    return invoice


def register_payment(db: Session, invoice_id: int, amount: float) -> models.Invoice:
    inv = db.get(models.Invoice, invoice_id)
    if not inv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الفاتورة غير موجودة")
    if amount > float(inv.total) - float(inv.paid_amount):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "المبلغ أكبر من المتبقي على الفاتورة")
    inv.paid_amount = float(inv.paid_amount) + amount
    if float(inv.paid_amount) >= float(inv.total):
        inv.status = "paid"
    db.commit()
    db.refresh(inv)
    return inv


def list_invoices(db: Session):
    return list(db.scalars(select(models.Invoice).order_by(models.Invoice.id.desc())))


# ---------- مساعدات داخلية ----------
def _get_item(db: Session, item_id: int):
    from backend.modules.inventory import models as inv_models
    return db.get(inv_models.Item, item_id)


def _item_cost(db: Session, item_id: int) -> float:
    return float(_get_item(db, item_id).purchase_price or 0)


def _revenue_account(db: Session) -> int:
    from backend.modules.accounting import models as acc_models
    acct = db.scalar(select(acc_models.Account).where(acc_models.Account.code == "4100"))
    if not acct:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "حساب الإيرادات (كود 4100) غير معرّف في دليل الحسابات")
    return acct.id


def _tax_account(db: Session) -> int | None:
    from backend.modules.accounting import models as acc_models
    acct = db.scalar(select(acc_models.Account).where(acc_models.Account.code == "2200"))
    return acct.id if acct else None
