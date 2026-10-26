# -*- coding: utf-8 -*-
"""قسم المحاسبة — منطق الأعمال."""
from __future__ import annotations

import datetime as dt

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.modules.accounting import models, schemas


def list_accounts(db: Session, params: schemas.PageParams | None = None):
    stmt = select(models.Account).order_by(models.Account.code)
    if params and params.search:
        like = f"%{params.search}%"
        stmt = stmt.where(models.Account.name_ar.like(like) | models.Account.code.like(like))
    return list(db.scalars(stmt))


def create_account(db: Session, data: schemas.AccountCreate) -> models.Account:
    exists = db.scalar(select(models.Account).where(models.Account.code == data.code))
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, f"كود الحساب {data.code} مستخدم بالفعل")
    obj = models.Account(**data.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def update_account(db: Session, account_id: int, data: schemas.AccountUpdate) -> models.Account:
    obj = db.get(models.Account, account_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الحساب غير موجود")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return obj


def delete_account(db: Session, account_id: int) -> None:
    obj = db.get(models.Account, account_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الحساب غير موجود")
    if obj.journal_lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "لا يمكن حذف حساب عليه قيود")
    db.delete(obj)
    db.commit()


def create_journal_entry(db: Session, data: schemas.JournalEntryCreate,
                         user: dict, source_module: str | None = None,
                         source_ref: str | None = None) -> models.JournalEntry:
    """تسجيل قيد يومية — يلتزم بمبادئ القيد المزدوج (Double-Entry Bookkeeping):

    1. التوازن: مجموع المدين = مجموع الدائن (مع tolerance ضئيل للأخطاء العشرية).
    2. عدم الصفر: القيد يجب أن يحمل حركة فعلية (مدين أو دائن > 0).
    3. كل سطر له طرف واحد فقط: مدين أو دائن — لا يجمع الطرفين معًا.
    4. التحقق المسبق من وجود الحسابات قبل الإنشاء (Atomicity).
    """
    total_debit = round(sum(l.debit for l in data.lines), 4)
    total_credit = round(sum(l.credit for l in data.lines), 4)
    if abs(total_debit - total_credit) > 0.0001:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"القيد غير متوازن: مدين {total_debit} <> دائن {total_credit}")
    if total_debit <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "لا يمكن تسجيل قيد بقيمة صفرية")
    for line in data.lines:
        if line.debit > 0 and line.credit > 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "سطر القيد يحمل مدينًا ودائنًا معًا — افصلهما في سطرين")

    seq = (db.scalar(select(func.count(models.JournalEntry.id))) or 0) + 1
    entry_no = f"JE-{dt.date.today():%Y%m%d}-{seq:05d}"

    # التحقق من وجود كل الحسابات أولًا قبل إنشاء أي كائن،
    # حتى لا يبقى قيد ناقص في الجلسة عند رفع خطأ.
    lines_ = []
    for line in data.lines:
        acct = db.get(models.Account, line.account_id)
        if not acct:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"الحساب رقم {line.account_id} غير موجود في دليل الحسابات")
        if not acct.is_active:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"الحساب {acct.code} موقوف — لا يُرحَّل عليه قيود")
        lines_.append(models.JournalLine(
            account_id=line.account_id, debit=line.debit, credit=line.credit))

    entry = models.JournalEntry(
        entry_no=entry_no,
        date=dt.datetime.combine(data.date, dt.time()),
        description=data.description,
        source_module=source_module,
        source_ref=source_ref,
        created_by=user.get("user_id"),
        lines=lines_,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


# ======================================================================
# أدوات الفترات والمقارنة (Comparative Financial Statements)
# ======================================================================

def _shift_year(d: dt.date, years: int) -> dt.date:
    """زيح تاريخًا عدد سنوات للخلف/الأمام مع معالجة 29 فبراير."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29/2 في سنة غير كبيسة
        return d.replace(year=d.year + years, day=28)


def comparative_periods(date_from: dt.date, date_to: dt.date,
                        comparison_type: str = "yoy",
                        custom_from: dt.date | None = None,
                        custom_to: dt.date | None = None) -> dict:
    """يحسب الفترة الحالية والفترة المقارنة ونهاياتهما (لأرصدة الميزانية).

    - yoy   : نفس الفترة من السنة السابقة.
    - mom   : نفس الفترة من الشهر السابق.
    - custom: فترة مخصصة يختارها المستخدم.
    """
    if comparison_type == "mom":
        months_back = max(1, round((date_to.year - date_from.year) * 12
                                   + (date_to.month - date_from.month)) + 1)
        y, m = date_from.year, date_from.month - months_back
        while m <= 0:
            m += 12; y -= 1
        import calendar as _cal
        prev_day = min(date_from.day, _cal.monthrange(y, m)[1])
        p_from = dt.date(y, m, prev_day)
        y2, m2 = date_to.year, date_to.month - months_back
        while m2 <= 0:
            m2 += 12; y2 -= 1
        p_to = dt.date(y2, m2, min(date_to.day, _cal.monthrange(y2, m2)[1]))
    elif comparison_type == "custom" and custom_from and custom_to:
        p_from, p_to = custom_from, custom_to
    else:  # yoy افتراضيًا
        p_from, p_to = _shift_year(date_from, -1), _shift_year(date_to, -1)
    return {
        "current": {"from": date_from, "to": date_to},
        "prior": {"from": p_from, "to": p_to},
        # أرصدة الميزانية تُقاس من بداية النظام حتى نهاية كل فترة
        "current_cutoff": date_to,
        "prior_cutoff": p_to,
    }


def _add_variants(current: float, prior: float) -> dict:
    """قيمة التغير ونسبته — النسبة لا تُحسب إذا كان أساس الفترة السابقة صفرًا."""
    change = round(current - prior, 4)
    pct = round(change / abs(prior) * 100, 2) if prior not in (0, 0.0) else None
    return {"current": current, "prior": prior, "change": change, "change_pct": pct}


def _account_balances_asof(db: Session, cutoff: dt.date | None) -> dict[int, dict]:
    """أرصدة كل الحسابات تراكميًا حتى تاريخ معين (شاملًا الأرصدة الافتتاحية)."""
    stmt = (
        select(
            models.JournalLine.account_id,
            func.coalesce(func.sum(models.JournalLine.debit), 0).label("debit"),
            func.coalesce(func.sum(models.JournalLine.credit), 0).label("credit"),
        )
        .join(models.JournalEntry, models.JournalEntry.id == models.JournalLine.entry_id)
        .group_by(models.JournalLine.account_id)
    )
    if cutoff:
        stmt = stmt.where(models.JournalEntry.date
                          < dt.datetime.combine(cutoff + dt.timedelta(days=1), dt.time()))
    out: dict[int, dict] = {}
    for r in db.execute(stmt).all():
        out[r.account_id] = {"debit": float(r.debit), "credit": float(r.credit)}
    return out


def _period_activity(db: Session, p_from: dt.date, p_to: dt.date) -> dict[int, dict]:
    """حركة كل حساب خلال فترة محددة فقط (لمنطق التدفقات النقدية)."""
    stmt = (
        select(
            models.JournalLine.account_id,
            func.coalesce(func.sum(models.JournalLine.debit), 0).label("debit"),
            func.coalesce(func.sum(models.JournalLine.credit), 0).label("credit"),
        )
        .join(models.JournalEntry, models.JournalEntry.id == models.JournalLine.entry_id)
        .where(models.JournalEntry.date >= dt.datetime.combine(p_from, dt.time()))
        .where(models.JournalEntry.date <= dt.datetime.combine(p_to, dt.time()) + dt.timedelta(microseconds=-1))
        .group_by(models.JournalLine.account_id)
    )
    out: dict[int, dict] = {}
    for r in db.execute(stmt).all():
        out[r.account_id] = {"debit": float(r.debit), "credit": float(r.credit)}
    return out


def unbalanced_entries(db: Session, p_from: dt.date | None = None,
                       p_to: dt.date | None = None) -> list[dict]:
    """قيد يومية غير متوازن = مدين ≠ دائن.

    هذا الفحص هو مصدر أخطاء شائعة في القوائم المالية: قيد يدوي ناقص الجانب
    يجعل مصروفات الفترة منتفخة أو الإيرادات صفرًا مع بقاء الأرصدة الشكلية،
    فيظهر النظام ربحًا لا ينتج من المعادلة المعروضة على الشاشة.
    يعيد قائمة بالقيد المخالف وقيمتي المدين والدائن والفارق بينهما.
    """
    stmt = (
        select(
            models.JournalEntry.id.label("entry_id"),
            models.JournalEntry.entry_no,
            models.JournalEntry.date,
            models.JournalEntry.description,
            models.JournalEntry.source_module,
            func.coalesce(func.sum(models.JournalLine.debit), 0).label("total_debit"),
            func.coalesce(func.sum(models.JournalLine.credit), 0).label("total_credit"),
        )
        .join(models.JournalLine, models.JournalLine.entry_id == models.JournalEntry.id)
        .group_by(models.JournalEntry.id)
    )
    if p_from:
        stmt = stmt.where(models.JournalEntry.date >= dt.datetime.combine(p_from, dt.time()))
    if p_to:
        stmt = stmt.where(models.JournalEntry.date <= dt.datetime.combine(p_to, dt.time()) + dt.timedelta(microseconds=-1))
    bad = []
    for r in db.execute(stmt).all():
        d, cr = float(r.total_debit), float(r.total_credit)
        if abs(d - cr) > 0.005:
            bad.append({"entry_id": r.entry_id, "entry_no": r.entry_no,
                        "date": r.date.isoformat() if isinstance(r.date, (dt.date, dt.datetime)) else str(r.date),
                        "description": r.description, "source_module": r.source_module,
                        "total_debit": round(d, 4), "total_credit": round(cr, 4),
                        "difference": round(d - cr, 4)})
    return bad


def _natural(a_type: str) -> int:
    """الرصيد الطبيعي: الأصول والمصروفات مدينة، والبقية دائنة."""
    return 1 if a_type in ("asset", "expense") else -1


def _load_accounts(db: Session) -> list[models.Account]:
    return list(db.scalars(select(models.Account).order_by(models.Account.code)))


def income_statement_comparative(db: Session, date_from: dt.date, date_to: dt.date,
                                 comparison_type: str = "yoy",
                                 custom_from: dt.date | None = None,
                                 custom_to: dt.date | None = None) -> dict:
    """قائمة دخل مقارنة: عمودان (الحالية vs السابقة) + قيمة ونسبة التغير."""
    per = comparative_periods(date_from, date_to, comparison_type, custom_from, custom_to)
    cur_act = _period_activity(db, per["current"]["from"], per["current"]["to"])
    pri_act = _period_activity(db, per["prior"]["from"], per["prior"]["to"])
    accounts = _load_accounts(db)

    def build(act: dict[int, dict], atype: str) -> tuple[list, float]:
        rows, total = [], 0.0
        for a in accounts:
            if a.account_type != atype:
                continue
            v = act.get(a.id, {"debit": 0.0, "credit": 0.0})
            # الرصيد بالأرقام الطبيعية: الإيرادات (دائنة) = الدائن - المدين
            sign = 1 if atype == "revenue" else -1
            bal = round((v["credit"] - v["debit"]) * sign, 4)
            if bal == 0 and not act:
                continue
            rows.append({"code": a.code, "name_ar": a.name_ar, "amount": bal})
            total += bal
        return rows, round(total, 4)

    cur_rev_rows, cur_rev = build(cur_act, "revenue")
    cur_exp_rows, cur_exp = build(cur_act, "expense")
    pri_rev_rows, pri_rev = build(pri_act, "revenue")
    pri_exp_rows, pri_exp = build(pri_act, "expense")

    def merged(rows_cur, rows_pri):
        pc = {r["code"]: r["amount"] for r in rows_pri}
        seen = {r["code"] for r in rows_cur}
        out = [dict(_add_variants(r["amount"], pc.get(r["code"], 0.0)),
                    code=r["code"], name_ar=r["name_ar"]) for r in rows_cur]
        # بنود موجودة في الفترة السابقة فقط (لمقارنة كاملة غير مبتورة)
        for r in rows_pri:
            if r["code"] not in seen:
                out.append(dict(_add_variants(0.0, r["amount"]),
                                code=r["code"], name_ar=r["name_ar"]))
        return out

    net_cur = round(cur_rev - cur_exp, 4)
    net_pri = round(pri_rev - pri_exp, 4)
    # فحص سلامة البيانات: أي قيد غير متوازن داخل الفترتين يجعل الأرقام المعروضة
    # لا تنتج معادلة الشاشة (مثل ربح لا يساوي الإيراد - المصروفات). يُدرج التحذير
    # في الاستجابة ليظهر على التقرير نفسه بدل الصمت.
    warnings = []
    for label, pf, pt in (("الفترة الحالية", per["current"]["from"], per["current"]["to"]),
                          ("فترة المقارنة", per["prior"]["from"], per["prior"]["to"])):
        for e in unbalanced_entries(db, pf, pt):
            warnings.append(
                f"قيد غير متوازن ({e['entry_no']}) في {label}: مدين {e['total_debit']:,.2f} "
                f"≠ دائن {e['total_credit']:,.2f} — الفرق {e['difference']:,.2f}. "
                f"صحّح القيد ثم أعد إصدار القائمة.")
    return {
        "title": "قائمة الدخل المقارنة",
        "data_warnings": warnings,
        "periods": {
            "current": {"from": per["current"]["from"].isoformat(),
                        "to": per["current"]["to"].isoformat()},
            "prior": {"from": per["prior"]["from"].isoformat(),
                      "to": per["prior"]["to"].isoformat()},
            "comparison_type": comparison_type,
        },
        "revenue": merged(cur_rev_rows, pri_rev_rows),
        "expenses": merged(cur_exp_rows, pri_exp_rows),
        "totals": {
            "revenue": _add_variants(cur_rev, pri_rev),
            "expenses": _add_variants(cur_exp, pri_exp),
            "net_income": _add_variants(net_cur, net_pri),
        },
    }


def balance_sheet_comparative(db: Session, current_cutoff: dt.date,
                              comparison_type: str = "yoy",
                              prior_cutoff: dt.date | None = None) -> dict:
    """قائمة مركز مالي مقارنة: أرصدة عند نهاية كل فترة + التغير وقيمته ونسبته."""
    if prior_cutoff is None:
        prior_cutoff = _shift_year(current_cutoff, -1)
    cur_bal = _account_balances_asof(db, current_cutoff)
    pri_bal = _account_balances_asof(db, prior_cutoff)
    accounts = _load_accounts(db)

    def snapshot(bals: dict[int, dict]) -> dict[str, list]:
        groups: dict[str, list] = {"asset": [], "liability": [], "equity": []}
        for a in accounts:
            if a.account_type not in groups:
                continue
            v = bals.get(a.id, {"debit": 0.0, "credit": 0.0})
            bal = round((v["debit"] - v["credit"]) * _natural(a.account_type), 4)
            if bal != 0:
                groups[a.account_type].append({"code": a.code, "name_ar": a.name_ar, "amount": bal})
        return groups

    g_cur, g_pri = snapshot(cur_bal), snapshot(pri_bal)

    def net_income_of(bals: dict[int, dict]) -> float:
        ni = 0.0
        for a in accounts:
            if a.account_type in ("revenue", "expense"):
                v = bals.get(a.id, {"debit": 0.0, "credit": 0.0})
                ni += (v["debit"] - v["credit"]) * _natural(a.account_type)
        return round(ni, 4)

    ni_cur, ni_pri = net_income_of(cur_bal), net_income_of(pri_bal)

    def section(atype: str) -> dict:
        pc = {r["code"]: r["amount"] for r in g_pri[atype]}
        rows = [dict(_add_variants(r["amount"], pc.get(r["code"], 0.0)),
                     code=r["code"], name_ar=r["name_ar"]) for r in g_cur[atype]]
        total_cur = round(sum(r["amount"] for r in g_cur[atype]), 4)
        total_pri = round(sum(r["amount"] for r in g_pri[atype]), 4)
        return {"rows": rows, "total": _add_variants(total_cur, total_pri)}

    assets = section("asset")
    liabilities = section("liability")
    equity = section("equity")
    tot_a_cur = assets["total"]["current"] + ni_cur
    tot_a_pri = assets["total"]["prior"] + ni_pri
    tot_le_cur = liabilities["total"]["current"] + equity["total"]["current"] + ni_cur
    tot_le_pri = liabilities["total"]["prior"] + equity["total"]["prior"] + ni_pri
    return {
        "title": "قائمة المركز المالي المقارنة",
        "periods": {
            "current_as_of": current_cutoff.isoformat(),
            "prior_as_of": prior_cutoff.isoformat(),
            "comparison_type": comparison_type,
        },
        "assets": assets,
        "liabilities": liabilities,
        "equity": equity,
        "retained_earnings": _add_variants(ni_cur, ni_pri),
        "totals": {
            "total_assets": _add_variants(tot_a_cur, tot_a_pri),
            "total_liabilities_equity": _add_variants(tot_le_cur, tot_le_pri),
            "balanced": abs(tot_a_cur - tot_le_cur) < 0.01,
            "prior_balanced": abs(tot_a_pri - tot_le_pri) < 0.01,
        },
    }


def cash_flow_comparative(db: Session, date_from: dt.date, date_to: dt.date,
                          comparison_type: str = "yoy",
                          custom_from: dt.date | None = None,
                          custom_to: dt.date | None = None) -> dict:
    """قائمة التدفقات النقدية بطريقة غير مباشرة (Indirect Method) مقارنة.

    تبدأ بصافي الدخل ثم تعدّل البنود غير النقدية وتغيّرات رأس المال العامل
    من فروق الأرصدة بين نهايتي الفترة، لكلتا الفترتين المعروضتين.
    """
    per = comparative_periods(date_from, date_to, comparison_type, custom_from, custom_to)
    accounts = _load_accounts(db)

    CASH_CODES = {"1100", "1110"}          # البنك والصندوق الرئيسي
    NONCASH_CURRENT = {"1400"}             # شيكات تحت التحصيل (رأس مال عامل)

    def statement_for(p_from: dt.date, p_to: dt.date) -> dict:
        end = _account_balances_asof(db, p_to)
        start = _account_balances_asof(db, p_from - dt.timedelta(days=1))
        act = _period_activity(db, p_from, p_to)

        def bal(bals, code, sign=None):
            acct = next((a for a in accounts if a.code == code), None)
            if not acct:
                return 0.0
            v = bals.get(acct.id, {"debit": 0.0, "credit": 0.0})
            s = _natural(acct.account_type) if sign is None else sign
            return round((v["debit"] - v["credit"]) * s, 4)

        def delta(code):
            return round(bal(end, code) - bal(start, code), 4)

        # صافي الدخل من حركة الفترة نفسها
        ni = 0.0
        for a in accounts:
            if a.account_type in ("revenue", "expense"):
                v = act.get(a.id, {"debit": 0.0, "credit": 0.0})
                ni += (v["debit"] - v["credit"]) * _natural(a.account_type)
        ni = round(ni, 4)

        lines = []

        def add(label: str, amount: float):
            amount = round(amount, 4)
            if amount != 0:
                lines.append({"label": label, "amount": amount})

        add("صافي الدخل عن الفترة", ni)
        # بنود غير نقدية: خسائر/أرباح الجرد تُعدّل عكسياً لأنها ضمن المصروفات/الإيرادات
        add("خسائر جرد المخزون (بند غير نقدي)", -delta("5910") if _has(accounts, "5910") else 0)
        add("أرباح جرد المخزون (بند غير نقدي)", delta("7700") if _has(accounts, "7700") else 0)
        add("عمولات وبطاقات محصلة بالنيابة", 0)
        # تغيّرات رأس المال العامل
        add("التغير في العملاء (المدينون)", -delta("1200") if _has(accounts, "1200") else 0)
        add("التغير في المخزون", -delta("1300") if _has(accounts, "1300") else 0)
        add("التغير في شيكات تحت التحصيل", -delta("1400"))
        add("التغير في الموردون (الدائنون)", delta("2100") if _has(accounts, "2100") else 0)
        add("التغير في ضريبة المبيعات المستحقة", delta("2200") if _has(accounts, "2200") else 0)
        add("التغير في أوراق الدفع (شيكات صادرة)", delta("2300") if _has(accounts, "2300") else 0)

        operating_total = round(sum(l["amount"] for l in lines), 4)
        # أنشطة استثمارية/تمويلية مبسطة: أي حركة على حسابات خارج التشغيل
        inv_fin = []
        for a in accounts:
            if a.account_type in ("asset", "liability", "equity") \
               and a.code not in CASH_CODES | NONCASH_CURRENT \
               and a.code not in {"1200", "1300", "1400", "2100", "2200", "2300", "5910", "7700"}:
                v = act.get(a.id, {"debit": 0.0, "credit": 0.0})
                amt = round((v["debit"] - v["credit"]) * -_natural(a.account_type), 4)
                if amt:
                    inv_fin.append({"label": f"{a.name_ar}", "amount": amt})
        financing_total = round(sum(x["amount"] for x in inv_fin), 4)
        net_change = round(operating_total + financing_total, 4)
        cash_delta = round((bal(end, "1100") + (bal(end, "1110") if _has(accounts, "1110") else 0))
                           - (bal(start, "1100") + (bal(start, "1110") if _has(accounts, "1110") else 0)), 4)
        return {
            "period": {"from": p_from.isoformat(), "to": p_to.isoformat()},
            "operating": {"lines": lines, "total": operating_total},
            "investing_financing": {"lines": inv_fin, "total": financing_total},
            "net_change_per_statement": net_change,
            "net_change_per_cash_accounts": cash_delta,
            "reconciles": abs(net_change - cash_delta) < 0.01,
        }

    cur = statement_for(per["current"]["from"], per["current"]["to"])
    pri = statement_for(per["prior"]["from"], per["prior"]["to"])

    def merged_lines():
        pri_map = {l["label"]: l["amount"] for l in pri["operating"]["lines"]}
        seen = set()
        rows = []
        for l in cur["operating"]["lines"]:
            rows.append(dict(_add_variants(l["amount"], pri_map.get(l["label"], 0.0)),
                             label=l["label"]))
            seen.add(l["label"])
        for l in pri["operating"]["lines"]:
            if l["label"] not in seen:
                rows.append(dict(_add_variants(0.0, l["amount"]), label=l["label"]))
        return rows

    return {
        "title": "قائمة التدفقات النقدية المقارنة (طريقة غير مباشرة)",
        "periods": {
            "current": per["current"], "prior": per["prior"],
            "comparison_type": comparison_type,
        },
        "lines": merged_lines(),
        "operating_total": _add_variants(cur["operating"]["total"], pri["operating"]["total"]),
        "investing_financing_total": _add_variants(cur["investing_financing"]["total"],
                                                   pri["investing_financing"]["total"]),
        "net_change": _add_variants(cur["net_change_per_statement"], pri["net_change_per_statement"]),
        "detail": {"current": cur, "prior": pri},
    }


def _has(accounts: list[models.Account], code: str) -> bool:
    return any(a.code == code for a in accounts)


def trial_balance(db: Session, date_from: dt.date | None = None,
                  date_to: dt.date | None = None):
    stmt = (
        select(
            models.Account.id,
            models.Account.code,
            models.Account.name_ar,
            func.coalesce(func.sum(models.JournalLine.debit), 0).label("debit"),
            func.coalesce(func.sum(models.JournalLine.credit), 0).label("credit"),
        )
        .join(models.JournalLine, models.JournalLine.account_id == models.Account.id, isouter=True)
        .join(models.JournalEntry, models.JournalEntry.id == models.JournalLine.entry_id, isouter=True)
        .group_by(models.Account.id)
        .order_by(models.Account.code)
    )
    if date_from:
        stmt = stmt.where(func.date(models.JournalEntry.date) >= dt.datetime.combine(date_from, dt.time()))
    if date_to:
        stmt = stmt.where(func.date(models.JournalEntry.date) <= dt.datetime.combine(date_to, dt.time()))
    rows = db.execute(stmt).all()
    return [
        {
            "account_id": r.id, "code": r.code, "name_ar": r.name_ar,
            "debit": float(r.debit), "credit": float(r.credit),
            "balance": float(r.debit) - float(r.credit),
        }
        for r in rows
    ]
