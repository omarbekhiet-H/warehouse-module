# -*- coding: utf-8 -*-
"""قسم الخزينة والمالية — منطق الأعمال والترحيل المحاسبي.

التكامل مع بقية الأقسام يتم حصريًا عبر طبقات الخدمات (services→services):
    treasury.services ──> accounting.services  (القيود/الأرصدة)
                      └─> sales.services       (إسقاط التحصيل على فواتير العملاء FIFO)
                      └─> purchasing.services  (إسقاط الدفع على فواتير الموردين FIFO)

خريطة الحسابات القياسية المستخدمة (تُنشأ تلقائيًا إن لم تكن موجودة):
    1100 النقدية بالصندوق (Cash on Hand)            asset
    1110 البنك (ح. جاري بنكي)                       asset
    1200 ضريبة قيمة مضافة (مدخلات)                  asset
    1300 المخزون                                    asset
    2100 مخزون تحت التسوية GRNI                     liability
    2200 ضريبة قيمة مضافة (مخرجات)                  liability
    1400 شيكات تحت التحصيل (شيكات واردة لم تُقبَّض) asset
    2300 أوراق دفع — شيكات صادرة (ليست بعدُ مدفوعة) liability
    5920 عمولات ومصروفات بنكية                      expense
    5930 عجز نقدية (Shortage in Cash)               expense
    7710 تجاوز نقدية (Overage in Cash)              revenue
    4100 إيرادات / 5xxx مصروفات ... إلخ
"""
from __future__ import annotations

import datetime as dt

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.modules.accounting import schemas as acc_schemas
from backend.modules.accounting import services as acc_services
from backend.modules.treasury import models, schemas

# ------------------------------------------------------------------
# دليل حسابات الخزينة — دوال مساعدة داخلية
# ------------------------------------------------------------------

def _acct(db: Session, code: str, name_ar: str, typ: str) -> int:
    """يضمن وجود حساب في دليل الحسابات ويعيد معرفه (عبر خدمات المحاسبة)."""
    from backend.modules.accounting import models as acc_models
    acct = db.scalar(select(acc_models.Account).where(acc_models.Account.code == code))
    if not acct:
        acct = acc_services.create_account(
            db, acc_schemas.AccountCreate(code=code, name_ar=name_ar, account_type=typ))
    return acct.id


def cash_gl(db: Session) -> int:
    return _acct(db, "1100", "النقدية بالصندوق (Cash on Hand)", "asset")


def bank_gl(db: Session) -> int:
    return _acct(db, "1110", "البنك (حساب جاري)", "asset")


def checks_under_collection_gl(db: Session) -> int:
    return _acct(db, "1400", "شيكات تحت التحصيل", "asset")


def outstanding_checks_gl(db: Session) -> int:
    return _acct(db, "2300", "أوراق دفع — شيكات صادرة", "liability")


def bank_fees_gl(db: Session) -> int:
    return _acct(db, "5920", "عمولات ومصاريف بنكية", "expense")


def shortage_gl(db: Session) -> int:
    return _acct(db, "5930", "عجز نقدية بالعهد (Shortage in Cash)", "expense")


def overage_gl(db: Session) -> int:
    return _acct(db, "7710", "تجاوز نقدية بالعهد", "revenue")


def misc_expense_gl(db: Session) -> int:
    return _acct(db, "5300", "مصروفات نثرية ومتنوعة", "expense")


def _journal(db: Session, lines: list[tuple[int, float, float]], desc: str,
             user: dict | None, source_ref: str | None, date: dt.date | None = None):
    """بوابة ترحيل موحّدة لكل قيود الخزينة إلى الأستاذ العام."""
    payload = acc_schemas.JournalEntryCreate(
        date=date or dt.date.today(),
        description=desc,
        lines=[acc_schemas.JournalLineIn(account_id=a, debit=d, credit=c)
               for a, d, c in lines],
    )
    return acc_services.create_journal_entry(
        db, payload, user or {"user_id": "system"},
        source_module="treasury", source_ref=source_ref)


def _next_no(db: Session, model, prefix: str) -> str:
    seq = (db.scalar(select(func.count(model.id))) or 0) + 1
    return f"{prefix}-{dt.date.today():%Y%m%d}-{seq:05d}"


def _resolve_party(db: Session, party_type: str | None, party_id: int | None):
    """يعيد (اسم الطرف، رقم حساب الأستاذ المدين/الدائن له) عبر خدمات الأقسام."""
    if party_type == "customer" and party_id:
        from backend.modules.sales import models as sal_models
        cust = db.get(sal_models.Customer, party_id)
        if not cust:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "العميل غير موجود")
        acct_id = cust.receivable_account_id or _acct(
            db, "1210", "العملاء (Accounts Receivable)", "asset")
        return cust.name_ar, acct_id
    if party_type == "supplier" and party_id:
        from backend.modules.purchasing import models as pur_models
        sup = db.get(pur_models.Supplier, party_id)
        if not sup:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "المورد غير موجود")
        acct_id = sup.payable_account_id
        if not sup.payable_account_id:
            acct_id = _acct(db, "2110", "الموردون (Accounts Payable)", "liability")
        return sup.name_ar, acct_id
    return None, None


# ------------------------------------------------------------------
# الصناديق والحسابات البنكية
# ------------------------------------------------------------------

def create_cash_account(db: Session, data: schemas.CashAccountCreate) -> models.CashAccount:
    if db.scalar(select(models.CashAccount).where(models.CashAccount.code == data.code)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"كود الخزينة {data.code} مستخدم")
    obj = models.CashAccount(**data.model_dump())
    if not obj.gl_account_id:
        obj.gl_account_id = cash_gl(db) if obj.account_kind == "cash" else bank_gl(db)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def list_cash_accounts(db: Session):
    return list(db.scalars(select(models.CashAccount)
                           .where(models.CashAccount.is_active.is_(True))
                           .order_by(models.CashAccount.code)))


def cash_book_balance(db: Session, cash_acc: models.CashAccount) -> float:
    """الرصيد الدفتري للصندوق/البنك من الأستاذ العام مباشرة (مدين − دائن)."""
    rows = acc_services.trial_balance(db)
    for r in rows:
        if r["account_id"] == cash_acc.gl_account_id:
            return round(r["balance"], 4)
    return 0.0


# ------------------------------------------------------------------
# الوردية (Shift Closing)
# ------------------------------------------------------------------

def open_shift(db: Session, data: schemas.ShiftOpen, user: dict) -> models.CashierShift:
    acc = db.get(models.CashAccount, data.cash_account_id)
    if not acc or acc.account_kind != "cash":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "حدد صندوقًا نقديًا صحيحًا للوردية")
    running = db.scalar(select(models.CashierShift).where(
        models.CashierShift.cash_account_id == acc.id,
        models.CashierShift.status == "open"))
    if running:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"توجد وردية مفتوحة بالفعل ({running.shift_no}) لنفس الصندوق")
    shift = models.CashierShift(
        shift_no=_next_no(db, models.CashierShift, "SH"),
        cash_account_id=acc.id, cashier=data.cashier,
        opening_float=data.opening_float, notes=data.notes,
        created_by=user.get("user_id"))
    db.add(shift)
    db.commit()
    db.refresh(shift)
    return shift


def close_shift(db: Session, shift_id: int, data: schemas.ShiftClose,
                user: dict) -> models.CashierShift:
    """إغلاق الوردية: الرصيد المتوقع = العهدة + المقبوضات النقدية − المدفوعات النقدية.
    الفرق بين العد الفعلي والمتوقع يُرحَّل محاسبيًا (عجز/تجاوز) وفق مبدأ حصر المسؤولية."""
    shift = db.get(models.CashierShift, shift_id)
    if not shift:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الوردية غير موجودة")
    if shift.status != "open":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "الوردية مغلقة مسبقًا")

    receipts = _shift_cash_sum(db, shift.id, "receipt")
    payments = _shift_cash_sum(db, shift.id, "payment")
    expected = round(float(shift.opening_float) + receipts - payments, 4)
    variance = round(data.counted_cash - expected, 4)

    shift.expected_cash = expected
    shift.counted_cash = data.counted_cash
    shift.variance = variance
    shift.status = "closed"
    shift.closed_at = dt.datetime.utcnow()
    if data.notes:
        shift.notes = data.notes

    acc = db.get(models.CashAccount, shift.cash_account_id)
    if abs(variance) >= 0.01:
        gl = acc.gl_account_id or cash_gl(db)
        if variance < 0:  # عجز: نقص النقدية الفعلي ⇒ مصروف
            _journal(db, [(shortage_gl(db), -variance, 0), (gl, 0, -variance)],
                     f"عجز نقدية وردية {shift.shift_no}", user, shift.shift_no)
        else:            # تجاوز: زيادة فعلية ⇒ إيراد عرضي
            _journal(db, [(gl, variance, 0), (overage_gl(db), 0, variance)],
                     f"تجاوز نقدية وردية {shift.shift_no}", user, shift.shift_no)
    db.commit()
    db.refresh(shift)
    return shift


def _shift_cash_sum(db: Session, shift_id: int, direction: str) -> float:
    val = db.scalar(select(func.coalesce(func.sum(models.Voucher.amount), 0)).where(
        models.Voucher.shift_id == shift_id,
        models.Voucher.direction == direction,
        models.Voucher.pay_method == "cash",
        models.Voucher.status == "posted"))
    return float(val or 0)


def list_shifts(db: Session):
    return list(db.scalars(select(models.CashierShift).order_by(models.CashierShift.id.desc())))


# ------------------------------------------------------------------
# سندات القبض والصرف بكافة طرق السداد
# ------------------------------------------------------------------

def create_voucher(db: Session, data: schemas.VoucherCreate, user: dict) -> models.Voucher:
    """ينشئ السند ويطرح قيده فورًا حسب طريقة السداد (متطلب الترحيل اللحظي للأستاذ)."""
    acc = db.get(models.CashAccount, data.account_id)
    if not acc or not acc.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "الصندوق/الحساب البنكي غير صحيح")
    if data.pay_method == "cash" and acc.account_kind != "cash":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "السداد النقدي يجب أن يكون على صندوق نقدي")
    if data.pay_method in ("check", "bank_transfer", "card") and acc.account_kind != "bank":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "الشيك/التحويل/البطاقة يجب أن يمر على حساب بنكي")
    if data.fee_amount >= data.amount:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "العمولة يجب أن تقل عن إجمالي المبلغ")

    voucher_date = data.date or dt.date.today()
    party_name, party_acct = _resolve_party(db, data.party_type, data.party_id)
    gl = acc.gl_account_id or (cash_gl(db) if acc.account_kind == "cash" else bank_gl(db))

    voucher = models.Voucher(
        voucher_no=_next_no(db, models.Voucher, "RV" if data.direction == "receipt" else "PV"),
        direction=data.direction, pay_method=data.pay_method,
        amount=data.amount, fee_amount=data.fee_amount,
        net_amount=round(data.amount - data.fee_amount, 4),
        date=voucher_date,
        party_type=data.party_type, party_id=data.party_id, party_name=party_name,
        counterparty_ref=data.counterparty_ref,
        source_module=data.source_module or "manual", source_ref=data.source_ref,
        account_id=acc.id, shift_id=data.shift_id,
        description=data.description, remarks=data.remarks,
        status="posted", created_by=user.get("user_id"),
    )

    # ربط الشيك/البطاقة ببياناتها
    check_obj = None
    if data.pay_method == "check":
        if not data.check_data:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "بيانات الشيك مطلوبة للسداد بالشيك")
        dup = db.scalar(select(models.Check).where(
            models.Check.check_no == data.check_data.check_no,
            models.Check.direction == ("in" if data.direction == "receipt" else "out")))
        if dup:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"رقم الشيك {dup.check_no} مسجل بالفعل بالحالة {dup.status}")
        check_obj = models.Check(
            check_no=data.check_data.check_no,
            direction="in" if data.direction == "receipt" else "out",
            amount=data.amount, bank_name=data.check_data.bank_name,
            issue_date=data.check_data.issue_date,
            party_type=data.party_type, party_id=data.party_id, party_name=party_name,
            image_path=data.check_data.image_path,
            status="registered" if data.direction == "receipt" else "issued",
            created_by=user.get("user_id"))
    elif data.pay_method == "card" and data.card_data:
        voucher.source_ref = (data.card_data.auth_code or "") + \
                             (f"/{data.card_data.reference_no}" if data.card_data.reference_no else "")

    lines_ = []
    # ---- بناء سطور القيد حسب الطريقة ----
    if data.direction == "receipt":
        if data.party_type == "customer" and party_acct:
            lines_.append((gl, (data.amount - data.fee_amount), 0))
            if data.fee_amount:
                lines_.append((bank_fees_gl(db), data.fee_amount, 0))
            lines_.append((party_acct, 0, data.amount))
        else:
            # قبض غير مرتبط بعميل (دفعة مقدمة/إيراد نقدي آخر) ⇒ حساب وسيط دائنون متنوعون
            suspense = _acct(db, "2120", "دائنون متنوعون (مقبوضات تحت التسوية)", "liability")
            lines_.append((gl, (data.amount - data.fee_amount), 0))
            if data.fee_amount:
                lines_.append((bank_fees_gl(db), data.fee_amount, 0))
            lines_.append((suspense, 0, data.amount))
    else:  # payment
        expense_acct = data.expense_account_id or misc_expense_gl(db)
        if data.party_type == "supplier" and party_acct:
            lines_.append((party_acct, data.amount, 0))
            lines_.append((gl, 0, (data.amount - data.fee_amount)))
            if data.fee_amount:
                lines_.append((bank_fees_gl(db), data.fee_amount, 0))
        else:
            lines_.append((expense_acct, data.amount, 0))
            lines_.append((gl, 0, (data.amount - data.fee_amount)))
            if data.fee_amount:
                lines_.append((bank_fees_gl(db), data.fee_amount, 0))

    entry = _journal(db, lines_,
                     f"{'سند قبض' if data.direction == 'receipt' else 'سند صرف'} "
                     f"{voucher.voucher_no} — {voucher.pay_method}"
                     + (f" — {party_name}" if party_name else ""),
                     user, voucher.voucher_no, voucher_date)
    voucher.journal_entry_id = entry.id
    if check_obj:
        voucher.check = check_obj
        check_obj.voucher = voucher
    db.add(voucher)
    db.flush()

    # ---- توزيع على الفواتير المفتوحة (FIFO) عبر خدمات الأقسام ----
    if data.counterparty_ref and data.party_type in ("customer", "supplier"):
        voucher.lines.append(models.VoucherAllocation(
            invoice_ref=data.counterparty_ref, allocated_amount=data.amount))
    elif data.party_type in ("customer", "supplier"):
        _auto_allocate_fifo(db, voucher, data.amount)

    db.commit()
    db.refresh(voucher)
    return voucher


def _auto_allocate_fifo(db: Session, voucher: models.Voucher, amount: float):
    """توزيع تلقائي بالأقدمية على فواتير العميل/المورد غير المسددة بالكامل."""
    remaining = amount
    if voucher.party_type == "customer":
        from backend.modules.sales import models as sal_models
        rows = db.execute(
            select(sal_models.Invoice).where(
                sal_models.Invoice.customer_id == voucher.party_id,
                sal_models.Invoice.status.in_(("posted", "partially_paid")),
            ).order_by(sal_models.Invoice.date)
        ).scalars().all()
        for inv in rows:
            if remaining <= 0:
                break
            due = float(inv.total) - float(inv.paid_amount)
            if due <= 0.005:
                continue
            take = round(min(due, remaining), 4)
            voucher.lines.append(models.VoucherAllocation(invoice_ref=inv.invoice_no,
                                                          allocated_amount=take))
            inv.paid_amount = float(inv.paid_amount) + take
            inv.status = "paid" if float(inv.paid_amount) >= float(inv.total) - 0.005 else "partially_paid"
            remaining = round(remaining - take, 4)
    elif voucher.party_type == "supplier":
        from backend.modules.purchasing import models as pur_models
        rows = db.execute(
            select(pur_models.PurchaseInvoice).where(
                pur_models.PurchaseInvoice.supplier_id == voucher.party_id,
                pur_models.PurchaseInvoice.status.in_(("posted", "partially_paid")),
            ).order_by(pur_models.PurchaseInvoice.date)
        ).scalars().all()
        for inv in rows:
            if remaining <= 0:
                break
            due = float(inv.total) - float(inv.paid_amount)
            if due <= 0:
                continue
            take = round(min(due, remaining), 4)
            voucher.lines.append(models.VoucherAllocation(invoice_ref=inv.invoice_no,
                                                          allocated_amount=take))
            inv.paid_amount = float(inv.paid_amount) + take
            inv.status = "paid" if float(inv.paid_amount) >= float(inv.total) - 0.005 else "partially_paid"
            remaining = round(remaining - take, 4)


def list_vouchers(db: Session, direction: str | None = None,
                  pay_method: str | None = None):
    stmt = select(models.Voucher).order_by(models.Voucher.id.desc())
    if direction:
        stmt = stmt.where(models.Voucher.direction == direction)
    if pay_method:
        stmt = stmt.where(models.Voucher.pay_method == pay_method)
    return list(db.scalars(stmt))


def cancel_voucher(db: Session, voucher_id: int, reason: str, user: dict) -> models.Voucher:
    """إلغاء سند بعكس قيده (لا حذف — مبدأ ورقة التدقيق Audit Trail)."""
    v = db.get(models.Voucher, voucher_id)
    if not v or v.status == "cancelled":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "السند غير موجود أو ملغى")
    if v.journal_entry_id:
        from backend.modules.accounting import models as acc_models
        orig = db.get(acc_models.JournalEntry, v.journal_entry_id)
        if orig:
            rev_lines = [(l.account_id, float(l.credit), float(l.debit)) for l in orig.lines]
            entry = _journal(db, rev_lines, f"عكس {v.voucher_no}: {reason}", user,
                             f"{v.voucher_no}-REV")
            v.journal_entry_id = entry.id
    # استرجاع التوزيعات على الفواتير
    for alloc in list(v.lines):
        _deallocate(db, v, alloc)
    v.status = "cancelled"
    v.remarks = (v.remarks or "") + f" | ملغي: {reason}"
    db.commit()
    db.refresh(v)
    return v


def _deallocate(db: Session, v: models.Voucher, alloc: models.VoucherAllocation):
    if v.party_type == "customer":
        from backend.modules.sales import models as sal_models
        inv = db.scalar(select(sal_models.Invoice).where(
            sal_models.Invoice.invoice_no == alloc.invoice_ref))
        if inv:
            inv.paid_amount = max(0.0, float(inv.paid_amount) - float(alloc.allocated_amount))
            inv.status = "posted" if float(inv.paid_amount) < float(inv.total) else inv.status
    elif v.party_type == "supplier":
        from backend.modules.purchasing import models as pur_models
        inv = db.scalar(select(pur_models.PurchaseInvoice).where(
            pur_models.PurchaseInvoice.invoice_no == alloc.invoice_ref))
        if inv:
            inv.paid_amount = max(0.0, float(inv.paid_amount) - float(alloc.allocated_amount))
            inv.status = "posted" if float(inv.paid_amount) < float(inv.total) else inv.status


# ------------------------------------------------------------------
# دورة الشيكات الواردة: تسجيل → حافظة/إيداع → تحصيل / ارتداد
# ------------------------------------------------------------------

def register_incoming_check(db: Session, data: schemas.IncomingCheckRegister,
                            user: dict) -> models.Check:
    """مرحلة 1: استلام شيك من عميل ⇒ مدين شيكات تحت التحصيل / دائن العميل."""
    dup = db.scalar(select(models.Check).where(models.Check.check_no == data.check_no,
                                               models.Check.direction == "in"))
    if dup:
        raise HTTPException(status.HTTP_409_CONFLICT, "رقم الشيك مسجل سابقًا")
    party_name, party_acct = None, None
    if data.customer_id:
        party_name, party_acct = _resolve_party(db, "customer", data.customer_id)
    else:
        party_name = data.customer_name
        party_acct = _acct(db, "1210", "العملاء (Accounts Receivable)", "asset")
    chk = models.Check(
        check_no=data.check_no, direction="in", amount=data.amount,
        bank_name=data.bank_name, issue_date=data.issue_date,
        party_type="customer", party_id=data.customer_id, party_name=party_name,
        status="registered", image_path=data.image_path,
        created_by=user.get("user_id"))
    _journal(db, [(checks_under_collection_gl(db), data.amount, 0),
                  (party_acct, 0, data.amount)],
             f"استلام شيك وارد {data.check_no} من {party_name or data.customer_name or '-'}",
             user, data.check_no)
    db.add(chk)
    db.commit()
    db.refresh(chk)
    return chk


def create_collection_batch(db: Session, data: schemas.BatchCreate,
                            user: dict) -> models.CheckBatch:
    """مرحلة 2: تجميع الشيكات المستحقة في حافظة وإيداعها بالبنك (بدون قيد — توقيت فقط)."""
    acc = db.get(models.CashAccount, data.bank_account_id)
    if not acc or acc.account_kind != "bank":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "حدد حسابًا بنكيًا صحيحًا")
    checks = list(db.scalars(select(models.Check).where(
        models.Check.id.in_(data.check_ids), models.Check.direction == "in")))
    if len(checks) != len(set(data.check_ids)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "بعض أرقام الشيكات غير موجودة أو غير واردة")
    for c in checks:
        if c.status != "registered":
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"الشيك {c.check_no} بحالة {c.status} — لا يدخل الحافظة")
    batch = models.CheckBatch(
        batch_no=_next_no(db, models.CheckBatch, "CB"),
        bank_account_id=acc.id,
        total_amount=round(sum(float(c.amount) for c in checks), 4),
        checks_count=len(checks), status="submitted",
        submitted_at=dt.datetime.utcnow(), remarks=data.remarks,
        created_by=user.get("user_id"))
    db.add(batch)
    db.flush()
    for c in checks:
        c.collection_batch_id = batch.id
        c.status = "deposited"
        c.deposit_date = dt.date.today()
    db.commit()
    db.refresh(batch)
    return batch


def settle_collection_batch(db: Session, batch_id: int, data: schemas.BatchSettle,
                            user: dict) -> models.CheckBatch:
    """مرحلة 3: إشعار البنك بالتحصبل ⇒ مدين البنك / دائن شيكات تحت التحصيل؛
    والمرتد يعود لعهدة العميل مدينًا بنفس القيمة (+ رسوم بنك إن وجدت)."""
    batch = db.get(models.CheckBatch, batch_id)
    if not batch:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الحافظة غير موجودة")
    if batch.status != "submitted":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "الحافظة مسوّاة أو غير مكتملة")
    bank_gl_id = batch.bank_account.gl_account_id or bank_gl(db)
    checks = list(db.scalars(select(models.Check).where(
        models.Check.collection_batch_id == batch.id)))
    bounced_ids = set(data.bounced_check_ids)
    settled_total = 0.0
    for c in checks:
        if c.id in bounced_ids:
            _, cust_acct = _resolve_party(db, c.party_type, c.party_id)
            if not cust_acct:
                cust_acct = _acct(db, "1210", "العملاء (Accounts Receivable)", "asset")
            lines_ = [(cust_acct, float(c.amount), 0),
                      (checks_under_collection_gl(db), 0, float(c.amount))]
            if data.bank_fees_total:
                lines_.insert(1, (bank_fees_gl(db), data.bank_fees_total, 0))
                lines_.append((bank_gl_id, 0, data.bank_fees_total))
            _journal(db, lines_, f"ارتداد شيك {c.check_no} — {c.reject_reason or 'رفض البنك'}",
                     user, c.check_no)
            c.status = "bounced"
            c.reject_reason = c.reject_reason or "رفض البنك — عدم كفاية الرصيد"
        else:
            c.status = "cleared"
            c.clearance_date = data.settled_date or dt.date.today()
            settled_total += float(c.amount)
    if settled_total:
        _journal(db, [(bank_gl_id, settled_total, 0),
                      (checks_under_collection_gl(db), 0, settled_total)],
                 f"تحصيل حافظة شيكات {batch.batch_no}", user, batch.batch_no)
    batch.status = "settled"
    batch.settled_at = dt.datetime.utcnow()
    db.commit()
    db.refresh(batch)
    return batch


# ------------------------------------------------------------------
# دورة الشيكات الصادرة: إصدار (دفتر صرف) → تسليم → صرف بالبنك / ارتداد
# ------------------------------------------------------------------

def create_payment_run(db: Session, data: schemas.PaymentRunCreate,
                       user: dict) -> models.PaymentRun:
    """إصدار شيكات للموردين: مدين المورد / دائن أوراق دفع (شيكات صادرة).
    لا يمس البنك إلا عند الصرف الفعلي — فصل الالتزام القانوني عن الحركة البنكية."""
    acc = db.get(models.CashAccount, data.bank_account_id)
    if not acc or acc.account_kind != "bank":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "حدد حسابًا بنكيًا للإصدار")
    run = models.PaymentRun(
        run_no=_next_no(db, models.PaymentRun, "PR"),
        bank_account_id=acc.id, status="issued", remarks=data.remarks,
        created_by=user.get("user_id"))
    db.add(run)
    db.flush()
    total = 0.0
    seen_nos: set[str] = set()
    for item in data.checks:
        if item.check_no in seen_nos:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"رقم شيك مكرر بالدفتر: {item.check_no}")
        seen_nos.add(item.check_no)
        party_name, party_acct = _resolve_party(db, "supplier", item.supplier_id)
        if not party_acct:
            party_name = item.supplier_name
            party_acct = _acct(db, "2110", "الموردون (Accounts Payable)", "liability")
        chk = models.Check(
            check_no=item.check_no, direction="out", amount=item.amount,
            bank_name=item.bank_name, issue_date=item.issue_date,
            party_type="supplier", party_id=item.supplier_id, party_name=party_name,
            status="issued", payment_run_id=run.id, created_by=user.get("user_id"))
        _journal(db, [(party_acct, item.amount, 0),
                      (outstanding_checks_gl(db), 0, item.amount)],
                 f"إصدار شيك صادر {item.check_no} لصالح {party_name or '-'}",
                 user, item.check_no)
        db.add(chk)
        total += float(item.amount)
    run.total_amount = round(total, 4)
    run.checks_count = len(data.checks)
    db.commit()
    db.refresh(run)
    return run


def act_on_check(db: Session, check_id: int, data: schemas.CheckAction,
                 user: dict) -> models.Check:
    """خط حالة موحد للشيكات الواردة والصادرة مع القيود المقابلة لكل انتقال."""
    chk = db.get(models.Check, check_id)
    if not chk:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الشيك غير موجود")
    today = dt.date.today()

    if chk.direction == "in":
        if data.action == "deposit":
            if chk.status != "registered":
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "الشيك ليس بحالة تسجيل")
            chk.status = "deposited"
            chk.deposit_date = today
        elif data.action == "clear":
            if chk.status != "deposited":
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "يجب إيداع الشيك أولًا")
            _journal(db, [(bank_gl(db), float(chk.amount), 0),
                          (checks_under_collection_gl(db), 0, float(chk.amount))],
                     f"تحصيل شيك وارد {chk.check_no}", user, chk.check_no)
            chk.status, chk.clearance_date = "cleared", today
        elif data.action == "bounce":
            if chk.status not in ("registered", "deposited"):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "لا يمكن ارتداد شيك مُحصَّل")
            _, cust_acct = _resolve_party(db, chk.party_type, chk.party_id)
            if not cust_acct:
                cust_acct = _acct(db, "1210", "العملاء (Accounts Receivable)", "asset")
            src = checks_under_collection_gl(db)
            lines_ = [(cust_acct, float(chk.amount), 0), (src, 0, float(chk.amount))]
            if data.bank_fee:
                lines_.insert(1, (bank_fees_gl(db), data.bank_fee, 0))
                lines_.append((bank_gl(db), 0, data.bank_fee))
            _journal(db, lines_, f"ارتداد شيك وارد {chk.check_no}: {data.reason or ''}",
                     user, chk.check_no)
            chk.status = "bounced"
            chk.reject_reason = data.reason
        else:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "إجراء غير صالح لشيك وارد")
    else:  # صادر
        if data.action == "deliver":
            if chk.status != "issued":
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "الشيك غير قابل للتسليم")
            chk.status = "delivered"
        elif data.action == "clear":
            if chk.status != "delivered":
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "يجب تسليم الشيك للمورد أولًا")
            _journal(db, [(outstanding_checks_gl(db), float(chk.amount), 0),
                          (bank_gl(db), 0, float(chk.amount))],
                     f"صرف شيك صادر {chk.check_no} من البنك", user, chk.check_no)
            chk.status, chk.clearance_date = "cleared", today
        elif data.action == "return":
            if chk.status not in ("issued", "delivered"):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "الشيك سبق صرفه")
            _, sup_acct = _resolve_party(db, chk.party_type, chk.party_id)
            if not sup_acct:
                sup_acct = _acct(db, "2110", "الموردون (Accounts Payable)", "liability")
            _journal(db, [(outstanding_checks_gl(db), float(chk.amount), 0),
                          (sup_acct, 0, float(chk.amount))],
                     f"إلغاء/ارتداد شيك صادر {chk.check_no}: {data.reason or ''}",
                     user, chk.check_no)
            chk.status = "returned"
            chk.reject_reason = data.reason
        elif data.action == "cancel":
            if chk.status in ("cleared", "returned", "cancelled"):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "لا يمكن إلغاء شيك منتهٍ")
            _, sup_acct = _resolve_party(db, chk.party_type, chk.party_id)
            if not sup_acct:
                sup_acct = _acct(db, "2110", "الموردون (Accounts Payable)", "liability")
            _journal(db, [(outstanding_checks_gl(db), float(chk.amount), 0),
                          (sup_acct, 0, float(chk.amount))],
                     f"إلغاء شيك صادر {chk.check_no}", user, chk.check_no)
            chk.status = "cancelled"
        else:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "إجراء غير صالح لشيك صادر")
    db.commit()
    db.refresh(chk)
    return chk


def list_checks(db: Session, direction: str | None = None, status_: str | None = None):
    stmt = select(models.Check).order_by(models.Check.id.desc())
    if direction:
        stmt = stmt.where(models.Check.direction == direction)
    if status_:
        stmt = stmt.where(models.Check.status == status_)
    return list(db.scalars(stmt))


# ------------------------------------------------------------------
# تسوية بطاقات الائتمان (Settlement): البنك يودع الصافي بعد العمولة
# ------------------------------------------------------------------

def settle_card_batches(db: Session, bank_account_id: int, fees_rate_pct: float,
                        user: dict) -> dict:
    """تسوية عمولات بطاقات على سندات قبض ببطاقة مُرحَّلة بالفعل بالإجمالي للبنك.

    المعالجة المحاسبية عند تسوية البنك (خصم العمولة من الإيداع):
        مدين 5920 عمولات ومصروفات بنكية / دائن 1110 البنك — بقيمة العمولة فقط،
        لأن إجمالي المبلغ كان أُودع افتراضيًا وقت السند، فيُصحَّح الفرق عند التسوية.
    """
    acc = db.get(models.CashAccount, bank_account_id)
    if not acc or acc.account_kind != "bank":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "حدد حسابًا بنكيًا")
    vouchers = list(db.scalars(select(models.Voucher).where(
        models.Voucher.direction == "receipt",
        models.Voucher.pay_method == "card",
        models.Voucher.status == "posted",
        models.Voucher.account_id == bank_account_id)))
    gross = sum(float(v.amount) for v in vouchers)
    fees = round(gross * fees_rate_pct / 100, 4)
    net = round(gross - fees, 4)
    if gross <= 0:
        return {"gross": 0, "fees": 0, "net": 0, "count": 0}
    _journal(db, [(bank_fees_gl(db), fees, 0),
                  (acc.gl_account_id or bank_gl(db), 0, fees)],
             f"تسوية عمولات بطاقات {acc.name_ar} ({fees_rate_pct}%)", user,
             f"CARDSETTLE-{dt.date.today():%Y%m%d}")
    return {"gross": gross, "fees": fees, "net": net, "count": len(vouchers)}


# ------------------------------------------------------------------
# التسويات البنكية (Bank Reconciliation)
# ------------------------------------------------------------------

def create_reconciliation(db: Session, data: schemas.ReconciliationCreate,
                          user: dict) -> models.Reconciliation:
    """يبني مربع التسوية: دفتري ± تعديلات الشركة = فعلي ± تعديلات البنك."""
    acc = db.get(models.CashAccount, data.bank_account_id)
    if not acc or acc.account_kind != "bank":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "حدد حسابًا بنكيًا")
    book = cash_book_balance(db, acc)
    recon = models.Reconciliation(
        recon_no=_next_no(db, models.Reconciliation, "BR"),
        bank_account_id=acc.id, period_from=data.period_from, period_to=data.period_to,
        book_balance=book, statement_balance=data.statement_balance,
        remarks=data.remarks, created_by=user.get("user_id"))
    db.add(recon)
    db.flush()
    company_adj = 0.0
    bank_adj = 0.0
    for it in data.items:
        signed = it.amount if it.sign == "+" else -it.amount
        if it.side == "book":
            company_adj += signed
            needs_j = it.item_type in ("bank_charge", "direct_debit_note",
                                       "interest_income", "other")
        else:
            bank_adj += signed
            needs_j = False
        recon.items.append(models.ReconciliationItem(
            item_type=it.item_type, side=it.side, amount=it.amount, sign=it.sign,
            reference=it.reference, description=it.description,
            requires_journal=needs_j))
    recon.company_adjustments_total = round(company_adj, 4)
    recon.bank_adjustments_total = round(bank_adj, 4)
    recon.adjusted_book_balance = round(book + company_adj, 4)
    recon.adjusted_statement_balance = round(data.statement_balance + bank_adj, 4)
    recon.is_balanced = abs(recon.adjusted_book_balance -
                            recon.adjusted_statement_balance) < 0.01
    db.commit()
    db.refresh(recon)
    return recon


def finalize_reconciliation(db: Session, recon_id: int,
                            data: schemas.ReconciliationFinalize,
                            user: dict) -> models.Reconciliation:
    """اعتماد التسوية مع ترحيل بنود الدفاتر غير المسجلة (مصروفات/إيرادات البنك)."""
    recon = db.get(models.Reconciliation, recon_id)
    if not recon:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "التسوية غير موجودة")
    if recon.status == "finalized":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "التسوية معتمدة مسبقًا")
    if not recon.is_balanced:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "مربع التسوية غير متوازن — راجع البنود قبل الاعتماد")
    if data.post_missing_journals:
        bank_gl_id = recon.bank_account.gl_account_id or bank_gl(db)
        for item in recon.items:
            if item.requires_journal and not item.journal_entry_id:
                signed = item.amount if item.sign == "+" else -item.amount
                if item.item_type == "bank_charge" or item.item_type == "other":
                    # خصمها البنك ولم تسجّل بالدفاتر ⇒ مدين المصروف / دائن البنك
                    _journal(db, [(bank_fees_gl(db), abs(signed), 0),
                                  (bank_gl_id, 0, abs(signed))],
                             f"مصروف بنكي — تسوية {recon.recon_no}", user, recon.recon_no,
                             recon.period_to)
                elif item.item_type == "interest_income":
                    _journal(db, [(bank_gl_id, abs(signed), 0),
                                  (overage_gl(db), 0, abs(signed))],
                             f"إيراد فوائد بنكية — تسوية {recon.recon_no}", user,
                             recon.recon_no, recon.period_to)
                elif item.item_type == "direct_debit_note":
                    _journal(db, [(misc_expense_gl(db), abs(signed), 0),
                                  (bank_gl_id, 0, abs(signed))],
                             f"إشعار مدينة بنكي — تسوية {recon.recon_no}", user,
                             recon.recon_no, recon.period_to)
    recon.status = "finalized"
    db.commit()
    db.refresh(recon)
    return recon


def import_statement_lines(db: Session, bank_account_id: int,
                           lines: list[schemas.StatementLineIn]) -> int:
    acc = db.get(models.CashAccount, bank_account_id)
    if not acc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الحساب البنكي غير موجود")
    n = 0
    for ln in lines:
        db.add(models.BankStatementLine(
            bank_account_id=acc.id, statement_date=ln.statement_date,
            description=ln.description, debit=ln.debit, credit=ln.credit))
        n += 1
    db.commit()
    return n


def list_reconciliations(db: Session):
    return list(db.scalars(select(models.Reconciliation).order_by(
        models.Reconciliation.id.desc())))
