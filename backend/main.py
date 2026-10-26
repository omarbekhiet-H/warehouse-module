# -*- coding: utf-8 -*-
"""نقطة الدخول الموحّدة للنظام (Application Factory).

كل قسم يظل مستقلًا بملفاته داخل backend/modules/<قسم>/، وهذا الملف
وظيفته الوحيدة: تجميع راوترات الأقسام تحت بادئة واحدة /api/v1،
مع إعداد CORS وتهيئة قاعدة البيانات عند الإقلاع.

التشغيل:
    uvicorn backend.main:app --reload --port 8000
الوثائق التفاعلية:
    http://localhost:8000/docs
"""
from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from backend.core.config import get_settings
from backend.core.database import Base, engine
from backend.modules.accounting import services as acc_svc


@asynccontextmanager
async def lifespan(app: FastAPI):
    # استيراد موديلات كل قسم أولًا حتى تُسجَّل جداولها في Base.metadata
    import backend.modules.accounting.models   # noqa: F401
    import backend.modules.inventory.models    # noqa: F401
    import backend.modules.sales.models        # noqa: F401
    import backend.modules.purchasing.models   # noqa: F401
    import backend.modules.treasury.models     # noqa: F401
    import backend.modules.hr.models           # noqa: F401
    Base.metadata.create_all(bind=engine)

    # ---- ترحيل مخططي خفيف لقاعدة SQLite القائمة (إضافة الأعمدة الجديدة فقط) ----
    _lightweight_migrate()
    yield


def _lightweight_migrate() -> None:
    """يضيف الأعمدة/الجداول الناقصة لقواعد SQLite القديمة دون فقدان البيانات.

    ملاحظة إنتاجية: يُفضَّل الانتقال إلى Alembic لإدارة المخططات بدل هذا الترحيل اليدوي.
    """
    from sqlalchemy import inspect, text

    if "sqlite" not in settings.DATABASE_URL:
        return  # على PostgreSQL يجب استخدام Alembic/Flyway
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())

    def add_columns(table: str, cols: list[tuple[str, str]]):
        if table not in existing_tables:
            return
        have = {c["name"] for c in insp.get_columns(table)}
        with engine.begin() as conn:
            for name, ddl in cols:
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))

    # ---- حركات المخزون ----
    add_columns("inv_movements", [
        ("dest_warehouse_id", "INTEGER"),
        ("source_doc", "VARCHAR(64)"),
        ("cost_source", "VARCHAR(16)"),
        ("valuation_value", "NUMERIC(18,4) DEFAULT 0"),
        ("is_posted", "BOOLEAN DEFAULT 1"),
    ])
    # ---- بطاقات الأصناف (التقييم والتكاليف — IAS 2) ----
    add_columns("inv_items", [
        ("standard_cost", "NUMERIC(18,4) DEFAULT 0"),
        ("costing_method", "VARCHAR(8) DEFAULT 'std'"),
        ("avg_cost", "NUMERIC(18,4) DEFAULT 0"),
        ("on_hand_qty", "NUMERIC(18,4) DEFAULT 0"),
        ("cogs_account_id", "INTEGER"),
        ("expense_account_id", "INTEGER"),
    ])
    # ---- الجرد وتسوياته ----
    add_columns("inv_stock_counts", [
        ("variance_value", "NUMERIC(18,4) DEFAULT 0"),
        ("posted_by", "VARCHAR(64)"),
        ("posted_at", "DATETIME"),
        ("auto_post_adjustments", "BOOLEAN DEFAULT 1"),
    ])
    add_columns("inv_count_lines", [("unit_cost", "NUMERIC(18,4) DEFAULT 0")])
    # ---- المشتريات: دورة PO → GRN → الفاتورة ----
    add_columns("pur_invoices", [("warehouse_id", "INTEGER"), ("po_id", "INTEGER")])
    add_columns("pur_order_lines", [("received_qty", "NUMERIC(18,4) DEFAULT 0")])
    add_columns("pur_receipts", [("supplier_invoice_ref", "VARCHAR(64)")])
    # ---- الموارد البشرية: ترقية الملف الوظيفي للموظف ----
    add_columns("hr_employees", [
        ("national_id", "VARCHAR(14)"),
        ("termination_date", "DATE"),
        ("department_id", "INTEGER"),
        ("branch_id", "INTEGER"),
        ("insurance_rate", "NUMERIC(6,3) DEFAULT 0.1"),
        ("tax_rate", "NUMERIC(6,3) DEFAULT 0"),
        ("payment_method", "VARCHAR(16) DEFAULT 'cash'"),
        ("bank_account_no", "VARCHAR(34)"),
        ("created_at", "DATETIME"),
    ])


settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _money(v: float) -> str:
    return f"{v:,.2f}"


def _safe(fn):
    """ينفذ استعلامًا ويعيد النتيجة أو None عند أي خطأ (لوحة عرض آمنة)."""
    try:
        return fn()
    except Exception:
        return None


@app.get("/", response_class=HTMLResponse, tags=["النظام"], summary="الشاشة الرئيسة (لوحة القيادة)")
def dashboard(request: Request):
    """الوضع النهائي للشاشة الرئيسة: بطاقة لكل موديول بنفس تنسيق التقارير
    (Bootstrap RTL / ألوان ERP) + دليل روابط شامل لكل الشاشات والتقارير المسجلة."""
    from pathlib import Path

    from fastapi.templating import Jinja2Templates
    from sqlalchemy import func, select as sa_select

    from backend.core.database import SessionLocal
    import backend.modules.accounting.models as acc_m
    import backend.modules.inventory.models as inv_m
    import backend.modules.sales.models as sal_m
    import backend.modules.purchasing.models as pur_m
    import backend.modules.treasury.models as tre_m
    import backend.modules.hr.models as hr_m
    from backend.modules.inventory import services as inv_svc
    from backend.modules.hr import services as hr_svc

    templates_dir = Path(__file__).resolve().parent / "templates"
    tpl = Jinja2Templates(directory=str(templates_dir))
    P = settings.API_V1_PREFIX
    today = dt.date.today()
    jan1 = dt.date(today.year, 1, 1).isoformat()
    prior_y = dt.date(today.year - 1, 12, 31).isoformat()
    today_s = today.isoformat()

    db = SessionLocal()
    modules = []
    try:
        # ---------- المحاسبة ----------
        tb_rows = _safe(lambda: acc_svc.trial_balance(db)) or []
        tot_dr = sum(float(r["debit"]) for r in tb_rows)
        tot_cr = sum(float(r["credit"]) for r in tb_rows)
        n_entries = _safe(lambda: db.scalar(sa_select(func.count(acc_m.JournalEntry.id)))) or 0
        n_accounts = _safe(lambda: db.scalar(sa_select(func.count(acc_m.Account.id)))) or 0
        modules.append({
            "name": "المحاسبة والأستاذ العام", "icon": "🧾", "color": "#1e5b93",
            "count_label": "قيود مرحّلة", "count": n_entries, "health_ok": True,
            "kpis": [
                {"label": "إجمالي مدين الأستاذ", "value": _money(tot_dr)},
                {"label": "إجمالي دائن الأستاذ", "value": _money(tot_cr)},
                {"label": "دليل الحسابات", "value": n_accounts},
            ],
            "links": [
                {"text": "📊 ميزان المراجعة", "url": f"{P}/accounting/reports/trial-balance"},
                {"text": "🖨️ المركز المالي (الميزانية)", "url": f"{P}/accounting/reports/print/balance-sheet?as_of={today_s}&prior_as_of={prior_y}"},
                {"text": "🖨️ قائمة الدخل المقارنة", "url": f"{P}/accounting/reports/print/income-statement?date_from={jan1}&date_to={today_s}"},
                {"text": "🖨️ التدفقات النقدية", "url": f"{P}/accounting/reports/print/cash-flow?date_from={jan1}&date_to={today_s}"},
                {"text": "📒 دفتر القيود اليومية (JSON)", "url": f"{P}/accounting/journal-entries"},
                {"text": "🏦 دليل الحسابات (JSON)", "url": f"{P}/accounting/accounts"},
            ],
        })

        # ---------- المخازن ----------
        val = _safe(lambda: inv_svc.valuation_report(db)) or {"total_value": 0, "lines": []}
        n_items = _safe(lambda: db.scalar(sa_select(func.count(inv_m.Item.id)))) or 0
        low = sum(1 for l in val.get("lines", []) if l.get("below_reorder"))
        modules.append({
            "name": "المخازن والجرد", "icon": "📦", "color": "#2e7d32",
            "count_label": "أصناف نشطة", "count": n_items, "health_ok": True,
            "kpis": [
                {"label": "تقييم المخزون الدفتري", "value": _money(float(val.get("total_value", 0)))},
                {"label": "أرصدة تحت حد الطلب", "value": low},
            ],
            "links": [
                {"text": "📈 تقرير الرصيد الحالي (JSON)", "url": f"{P}/inventory/reports/stock"},
                {"text": "💰 تقرير التقييم IAS 2 (JSON)", "url": f"{P}/inventory/reports/valuation"},
                {"text": "🔄 سجل الحركات (JSON)", "url": f"{P}/inventory/movements"},
                {"text": "📋 أوراق الجرد (JSON)", "url": f"{P}/inventory/counts"},
                {"text": "🏷️ بطاقات الأصناف (JSON)", "url": f"{P}/inventory/items"},
            ],
        })

        # ---------- المبيعات ----------
        n_sal = _safe(lambda: db.scalar(sa_select(func.count(sal_m.Invoice.id)))) or 0
        sal_posted = _safe(lambda: db.scalar(
            sa_select(func.sum(sal_m.Invoice.total))
            .where(sal_m.Invoice.status.in_(["posted", "paid"])))) or 0
        open_ar = _safe(lambda: db.scalar(
            sa_select(func.sum(sal_m.Invoice.total - sal_m.Invoice.paid_amount))
            .where(sal_m.Invoice.status == "posted"))) or 0
        modules.append({
            "name": "المبيعات والعملاء", "icon": "🛒", "color": "#ef6c00",
            "count_label": "فواتير مبيعات", "count": n_sal, "health_ok": True,
            "kpis": [
                {"label": "إجمالي الفواتير المرحّلة", "value": _money(float(sal_posted))},
                {"label": "ذمم مدينة مفتوحة", "value": _money(float(open_ar))},
            ],
            "links": [
                {"text": "🧾 فواتير البيع (JSON)", "url": f"{P}/sales/invoices"},
                {"text": "👥 العملاء وأرصدتهم (JSON)", "url": f"{P}/sales/customers"},
            ],
        })

        # ---------- المشتريات ----------
        n_po = _safe(lambda: db.scalar(sa_select(func.count(pur_m.PurchaseOrder.id)))) or 0
        n_pur_inv = _safe(lambda: db.scalar(sa_select(func.count(pur_m.PurchaseInvoice.id)))) or 0
        open_ap = _safe(lambda: db.scalar(
            sa_select(func.sum(pur_m.PurchaseInvoice.total - pur_m.PurchaseInvoice.paid_amount))
            .where(pur_m.PurchaseInvoice.status != "cancelled"))) or 0
        modules.append({
            "name": "المشتريات والموردون", "icon": "🚚", "color": "#6a1b9a",
            "count_label": "أوامر شراء", "count": n_po, "health_ok": True,
            "kpis": [
                {"label": "فواتير الشراء", "value": n_pur_inv},
                {"label": "ذمم دائنة مستحقة", "value": _money(float(open_ap))},
            ],
            "links": [
                {"text": "📝 أوامر الشراء (JSON)", "url": f"{P}/purchasing/orders"},
                {"text": "📥 أذون الاستلام GRN (JSON)", "url": f"{P}/purchasing/receipts"},
                {"text": "🧾 فواتير الشراء (JSON)", "url": f"{P}/purchasing/invoices"},
                {"text": "🤝 الموردون (JSON)", "url": f"{P}/purchasing/suppliers"},
            ],
        })

        # ---------- الخزينة ----------
        n_vou = _safe(lambda: db.scalar(sa_select(func.count(tre_m.Voucher.id)))) or 0
        receipts = _safe(lambda: db.scalar(
            sa_select(func.sum(tre_m.Voucher.amount_total)).where(
                tre_m.Voucher.direction == "receipt", tre_m.Voucher.status == "posted"))) or 0
        payments = _safe(lambda: db.scalar(
            sa_select(func.sum(tre_m.Voucher.amount_total)).where(
                tre_m.Voucher.direction == "payment", tre_m.Voucher.status == "posted"))) or 0
        n_checks = _safe(lambda: db.scalar(sa_select(func.count(tre_m.Check.id)))) or 0
        modules.append({
            "name": "الخزينة والتسويات البنكية", "icon": "💰", "color": "#00695c",
            "count_label": "سندات مسجّلة", "count": n_vou, "health_ok": True,
            "kpis": [
                {"label": "إجمالي المقبوضات", "value": _money(float(receipts))},
                {"label": "إجمالي المدفوعات", "value": _money(float(payments))},
                {"label": "الصافي النقدي", "value": _money(float(receipts) - float(payments))},
                {"label": "شيكات مسجّلة", "value": n_checks},
            ],
            "links": [
                {"text": "🏦 الصناديق والبنوك (JSON)", "url": f"{P}/treasury/accounts"},
                {"text": "🧾 سندات القبض والصرف (JSON)", "url": f"{P}/treasury/vouchers"},
                {"text": "📄 دورة الشيكات الواردة/الصادرة (JSON)", "url": f"{P}/treasury/checks"},
                {"text": "🔁 التسويات البنكية (JSON)", "url": f"{P}/treasury/reconciliations"},
                {"text": "⏱️ ورديات أمين الخزينة (JSON)", "url": f"{P}/treasury/shifts"},
            ],
        })

        # ---------- الموارد البشرية ----------
        n_emp = _safe(lambda: db.scalar(sa_select(func.count(hr_m.Employee.id)))) or 0
        active_loans = _safe(lambda: db.scalar(
            sa_select(func.count(hr_m.Loan.id)).where(hr_m.Loan.status.in_(["approved", "paying"])))) or 0
        cur_period = f"{today:%Y-%m}"
        sheet = _safe(lambda: hr_svc.payroll_sheet_report(db, cur_period)) or {"totals": {}, "count": 0}
        net_pay = float(sheet.get("totals", {}).get("net", 0))
        modules.append({
            "name": "الموارد البشرية والرواتب", "icon": "👥", "color": "#c62828",
            "count_label": "موظفون", "count": n_emp, "health_ok": True,
            "kpis": [
                {"label": "صافي مسير الشهر الحالي", "value": _money(net_pay)},
                {"label": "سلف/قروض جارية", "value": active_loans},
            ],
            "links": [
                {"text": "🗂️ ملف الموظفين (JSON)", "url": f"{P}/hr/employees"},
                {"text": "✅ سجل الحضور الشهري (JSON)", "url": f"{P}/hr/attendance?period={cur_period}"},
                {"text": "💸 السلف والقروض وأقساطها (JSON)", "url": f"{P}/hr/loans"},
                {"text": "🧮 مسيرات الرواتب (JSON)", "url": f"{P}/hr/payrolls"},
                {"text": "🖨️ كشف المرتبات للطباعة", "url": f"{P}/hr/reports/payroll-sheet/print?period={cur_period}"},
            ],
        })
    finally:
        db.close()

    link_index = [
        {"module": m["name"], "name": l["text"].split(" ", 1)[-1], "url": l["url"]}
        for m in modules for l in m["links"]
    ]
    return tpl.TemplateResponse("dashboard.html", {
        "request": request,
        "app_name": settings.APP_NAME,
        "version": "1.0.0",
        "prefix": P,
        "current_period": cur_period,
        "today_s": today_s,
        "jan1": jan1,
        "prior_y": prior_y,
        "db_url_short": settings.DATABASE_URL.replace("sqlite:///", "SQLite: "),
        "modules": modules,
        "link_index": link_index,
    })


@app.get("/index.json", tags=["النظام"], summary="فهرس JSON للأقسام والمسارات")
def root_json():
    return {
        "app": settings.APP_NAME,
        "dashboard": "/",
        "docs": "/docs",
        "modules": [
            f"{settings.API_V1_PREFIX}/accounting",
            f"{settings.API_V1_PREFIX}/inventory",
            f"{settings.API_V1_PREFIX}/sales",
            f"{settings.API_V1_PREFIX}/purchasing",
            f"{settings.API_V1_PREFIX}/treasury",
            f"{settings.API_V1_PREFIX}/hr",
        ],
    }


def register_routers(app: FastAPI) -> None:
    """تسجيل راوترات الأقسام — كل قسم في ملفاته الخاصة ولا يستورد من main."""
    from backend.modules.accounting.router import router as accounting_router
    from backend.modules.accounting.reports import router as accounting_reports_router
    from backend.modules.inventory.router import router as inventory_router
    from backend.modules.sales.router import router as sales_router
    from backend.modules.purchasing.router import router as purchasing_router
    from backend.modules.treasury.router import router as treasury_router

    app.include_router(accounting_router, prefix=settings.API_V1_PREFIX)
    app.include_router(accounting_reports_router, prefix=settings.API_V1_PREFIX)
    app.include_router(inventory_router, prefix=settings.API_V1_PREFIX)
    app.include_router(sales_router, prefix=settings.API_V1_PREFIX)
    app.include_router(purchasing_router, prefix=settings.API_V1_PREFIX)
    app.include_router(treasury_router, prefix=settings.API_V1_PREFIX)

    # قسم الموارد البشرية يُفعَّل تلقائيًا بمجرد إضافة router.py داخل مجلده
    try:
        from backend.modules.hr.router import router as hr_router
        app.include_router(hr_router, prefix=settings.API_V1_PREFIX)
    except ModuleNotFoundError:
        pass


register_routers(app)
