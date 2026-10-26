# -*- coding: utf-8 -*-
"""قسم المحاسبة — مركز طباعة القوائم المالية (Print Center).

يمثل صفحات HTML جاهزة للطباعة بتصميم Bootstrap RTL مطابق لهوية النظام،
وتدعم القوائم المقارنة (سنة حالية مقابل سنة مقارنة) مع أعمدة التغير.

النقاط:
  GET /api/v1/accounting/reports/print/income-statement?date_from=&date_to=&comparison_type=yoy&print=1
  GET /api/v1/accounting/reports/print/balance-sheet?as_of=&prior_as_of=&print=1
  GET /api/v1/accounting/reports/print/cash-flow?date_from=&date_to=&comparison_type=yoy&print=1

معامل print=1 يفتح الطباعة تلقائيًا في المتصفح (نافذة منبثقة أو نفس الصفحة).
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.core.config import get_current_user
from backend.core.database import get_db
from backend.modules.accounting import services

router = APIRouter(
    prefix="/accounting/reports/print",
    tags=["القوائم المالية — الطباعة"],
    dependencies=[Depends(get_current_user)],
)

templates = Jinja2Templates(directory="backend/templates")


def _fmt(v):
    """تنسيق الأرقام للعرض بفواصل الآلاف وبعشرتين."""
    if v is None:
        return "—"
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def _pct(v):
    if v is None:
        return "—"
    return f"{v:+.1f}%"


# إضافة المرشحات لبيئة القوالب
templates.env.filters["money"] = _fmt
templates.env.filters["pct"] = _fmt_pct = _pct


@router.get("/trial-balance", response_class=HTMLResponse, summary="شاشة ميزان المراجعة التفصيلي")
def trial_balance_screen(request: Request):
    """شاشة تفاعلية (Bootstrap) لميزان المراجعة: افتتاحي + حركة الفترة + ختامي،
    مع فلاتر (نوع القائمة/المستوى/إخفاء الأصفار) وشريط توازن.
    البيانات من نفس مصدر قائمة الدخل والميزانية — مطابقة حتمية."""
    return templates.TemplateResponse(request, "reports/trial_balance.html", {"request": request})


@router.get("/income-statement", response_class=HTMLResponse, summary="طباعة قائمة الدخل المقارنة")
def print_income_statement(
    request: Request,
    date_from: dt.date = Query(...),
    date_to: dt.date = Query(...),
    comparison_type: str = Query("yoy", pattern="^(yoy|mom|custom|none)$"),
    custom_from: dt.date | None = None,
    custom_to: dt.date | None = None,
    print_auto: bool = Query(False, alias="print"),
    db: Session = Depends(get_db),
):
    """قائمة الدخل — بعمود واحد عند comparison_type=none وبعمودين + تغير خلاف ذلك."""
    if comparison_type == "none":
        # إعادة استخدام منطق المقارنة مع فترة فارغة (بداية النظام) كمرجع صفري
        data = services.income_statement_comparative(
            db, date_from, date_to, "custom",
            dt.date(1900, 1, 1), dt.date(1900, 1, 2))
        comparative = False
        periods = {"current": {"from": date_from.isoformat(), "to": date_to.isoformat()},
                   "prior": None}
        rev_rows = [{**r, "prior": None, "change": None, "change_pct": None}
                    for r in data["revenue"]]
        exp_rows = [{**r, "prior": None, "change": None, "change_pct": None}
                    for r in data["expenses"]]
        totals = {k: {"current": v["current"]} for k, v in data["totals"].items()}
    else:
        data = services.income_statement_comparative(
            db, date_from, date_to, comparison_type, custom_from, custom_to)
        comparative = True
        periods = data["periods"]
        rev_rows, exp_rows = data["revenue"], data["expenses"]
        totals = data["totals"]

    return templates.TemplateResponse(request, "reports/income_statement.html", {
        "title": data.get("title", "قائمة الدخل"),
        "data_warnings": data.get("data_warnings", []),
        "periods": periods,
        "comparative": comparative,
        "revenue": rev_rows,
        "expenses": exp_rows,
        "totals": totals,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "print_auto": print_auto,
    })


@router.get("/balance-sheet", response_class=HTMLResponse, summary="طباعة المركز المالي المقارن")
def print_balance_sheet(
    request: Request,
    as_of: dt.date = Query(...),
    prior_as_of: dt.date | None = None,
    comparative: bool = Query(True),
    print_auto: bool = Query(False, alias="print"),
    db: Session = Depends(get_db),
):
    if comparative:
        data = services.balance_sheet_comparative(db, as_of, prior_cutoff=prior_as_of)
        sections = {k: data[k] for k in ("assets", "liabilities", "equity")}
        retained = data["retained_earnings"]
        totals = data["totals"]
        periods = data["periods"]
    else:
        data = services.balance_sheet(db, as_of)
        # تحويل القالب غير المقارن إلى نفس البنية
        def to_rows(items):
            return [{"code": i["code"], "name_ar": i["name_ar"],
                     "current": i["amount"], "prior": None,
                     "change": None, "change_pct": None} for i in items]
        sections = {
            "assets": {"rows": to_rows(data.get("assets", [])),
                       "total": {"current": data.get("total_assets", 0)}},
            "liabilities": {"rows": to_rows(data.get("liabilities", [])),
                            "total": {"current": data.get("total_liabilities", 0)}},
            "equity": {"rows": to_rows(data.get("equity", [])),
                       "total": {"current": data.get("total_equity", 0)}},
        }
        retained = {"current": data.get("net_income", 0)}
        ta = data.get("total_assets", 0)
        tle = data.get("total_liabilities", 0) + data.get("total_equity", 0) + data.get("net_income", 0)
        totals = {
            "total_assets": {"current": ta},
            "total_liabilities_equity": {"current": tle},
            "balanced": abs(ta - tle) < 0.01,
        }
        periods = {"current_as_of": as_of.isoformat(), "prior_as_of": None}

    return templates.TemplateResponse(request, "reports/balance_sheet.html", {
        "title": data.get("title", "قائمة المركز المالي"),
        "periods": periods,
        "comparative": comparative,
        "sections": sections,
        "retained": retained,
        "totals": totals,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "print_auto": print_auto,
    })


@router.get("/cash-flow", response_class=HTMLResponse, summary="طباعة التدفقات النقدية المقارنة")
def print_cash_flow(
    request: Request,
    date_from: dt.date = Query(...),
    date_to: dt.date = Query(...),
    comparison_type: str = Query("yoy", pattern="^(yoy|mom|custom)$"),
    custom_from: dt.date | None = None,
    custom_to: dt.date | None = None,
    print_auto: bool = Query(False, alias="print"),
    db: Session = Depends(get_db),
):
    data = services.cash_flow_comparative(db, date_from, date_to,
                                          comparison_type, custom_from, custom_to)
    cur = data["detail"]["current"]
    pri = data["detail"]["prior"]
    return templates.TemplateResponse(request, "reports/cash_flow.html", {
        "title": data["title"],
        "periods": {"current": cur["period"], "prior": pri["period"]},
        "lines": data["lines"],
        "operating_total": data["operating_total"],
        "investing_financing": cur["investing_financing"]["lines"],
        "investing_financing_prior": pri["investing_financing"]["lines"],
        "investing_financing_total": data["investing_financing_total"],
        "net_change": data["net_change"],
        "reconciles_cur": cur["reconciles"],
        "reconciles_pri": pri["reconciles"],
        "cash_delta_cur": cur["net_change_per_cash_accounts"],
        "cash_delta_pri": pri["net_change_per_cash_accounts"],
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "print_auto": print_auto,
    })
