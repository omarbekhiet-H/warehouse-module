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
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
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
    import backend.modules.auth.models           # noqa: F401
    Base.metadata.create_all(bind=engine)

    # ---- ترحيل مخططي خفيف لقاعدة SQLite القائمة (إضافة الأعمدة الجديدة فقط) ----
    _lightweight_migrate()

    # ---- Seed أول مستخدم admin + الأدوار الافتراضية عند أول تشغيل ----
    from backend.core.database import SessionLocal
    from backend.modules.auth import services as auth_svc
    _db = SessionLocal()
    try:
        auth_svc.bootstrap_admin(_db)
    finally:
        _db.close()
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
    # ---- المشتريات: دورة MR → PO → DN → GRN → الفاتورة (المطابقة الثلاثية) ----
    add_columns("pur_invoices", [
        ("warehouse_id", "INTEGER"), ("po_id", "INTEGER"),
        ("grn_id", "INTEGER"),
        ("freight_charges", "NUMERIC(18,4) DEFAULT 0"),
        ("customs_charges", "NUMERIC(18,4) DEFAULT 0"),
    ])
    add_columns("pur_orders", [
        ("mr_id", "INTEGER"),
        ("payment_terms", "VARCHAR(128)"),
    ])
    add_columns("pur_order_lines", [
        ("received_qty", "NUMERIC(18,4) DEFAULT 0"),
        ("mr_line_id", "INTEGER"),
        ("billed_qty", "NUMERIC(18,4) DEFAULT 0"),
    ])
    add_columns("pur_receipts", [
        ("supplier_invoice_ref", "VARCHAR(64)"),
        ("dn_id", "INTEGER"),
        ("inspector", "VARCHAR(128)"),
    ])
    add_columns("pur_receipt_lines", [("qty_rejected", "NUMERIC(18,4) DEFAULT 0")])
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


@app.get("/login", response_class=HTMLResponse, tags=["النظام"], summary="شاشة تسجيل الدخول")
def login_page(request: Request):
    """بوابة الدخول الرسمية — تُغلق الجلسة وتعرض نموذج Bootstrap بنفس هوية النظام.
    التوكن يُخزَّن في sessionStorage بالمتصفح وتستخدمه بقية الشاشات في ترويسة Authorization."""
    from pathlib import Path

    from fastapi.templating import Jinja2Templates
    tpl = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    # ملاحظة توافق: Starlette >= 0.38 يعتمد التوقيع (request, name, context)؛
    # القالب لا يحتاج "request" داخل الـ context لأنه يُضبط تلقائيًا.
    resp = tpl.TemplateResponse(request, "login.html", {
        "app_name": settings.APP_NAME, "version": "1.0.0",
    })
    resp.delete_cookie("erp_session")
    return resp


# ==================== شاشات الإدخال التفاعلية (HR وغيرها) ====================
# الملفات في backend/templates/pages وتُقدَّم من نفس المسارات القديمة التي يعرفها المستخدم
# (مثل /hr_employees.html) حتى لا تنكسر الروابط، مع دعم امتداد .html أو بدونه.
_PAGES_DIR = Path(__file__).parent / "templates" / "pages"

_PAGE_TITLES = {
    "hr_departments": ("الأقسام والفروع — الموارد البشرية", "الموارد البشرية"),
    "hr_employees":   ("إدارة الموظفين — الموارد البشرية", "الموارد البشرية"),
    "hr_attendance":  ("الحضور والانصراف — الموارد البشرية", "الموارد البشرية"),
    "hr_loans":       ("السلف والقروض — الموارد البشرية", "الموارد البشرية"),
    "hr_payroll":     ("مسير الرواتب — الموارد البشرية", "الموارد البشرية"),
}


def _page_response(page: str):
    """تقديم ملف HTML من مجلد pages بأمان (قائمة بيضاء — لا مسار عشوائي من المستخدم)."""
    from fastapi.responses import FileResponse
    if page not in _PAGE_TITLES:
        raise HTTPException(status_code=404, detail="الشاشة غير موجودة")
    fp = _PAGES_DIR / f"{page}.html"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"ملف الشاشة مفقود: {page}.html")
    return FileResponse(str(fp), media_type="text/html; charset=utf-8")


for _pg in list(_PAGE_TITLES):
    app.add_api_route(f"/{_pg}.html", lambda _p=_pg: _page_response(_p),
                      methods=["GET"], include_in_schema=False)
    app.add_api_route(f"/{_pg}", lambda _p=_pg: _page_response(_p),
                      methods=["GET"], include_in_schema=False)


@app.get("/api/v1/ui/screens", tags=["النظام"], summary="فهرس شاشات الإدخال المتاحة")
def ui_screens_index():
    return ApiResponse.ok([
        {"name": t[0], "module": t[1], "url": f"/{k}.html"} for k, t in _PAGE_TITLES.items()
    ])


# ملف JS المشترك للشاشات (ERP helper) — يُقدَّم مباشرة لضمان توفره بكل المسارات
@app.get("/templates/ui_base.js", include_in_schema=False)
def ui_base_js():
    from fastapi.responses import FileResponse
    fp = Path(__file__).parent / "templates" / "ui_base.js"
    if not fp.exists():
        raise HTTPException(status_code=404, detail="ui_base.js مفقود")
    return FileResponse(str(fp), media_type="application/javascript; charset=utf-8")


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
                {"text": "🖨️ قائمة الدخل المقارنة", "url": f"{P}/accounting/reports/print/income-statement?date_from={jan1}&date_to={today_s}&comparison_type=yoy"},
                {"text": "🖨️ التدفقات النقدية", "url": f"{P}/accounting/reports/print/cash-flow?date_from={jan1}&date_to={today_s}&comparison_type=yoy"},
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
                {"text": "🏢 الأقسام والفروع (شاشة إدخال)", "url": "/hr_departments.html"},
                {"text": "🗂️ شاشة الموظفين (تعيين/تعديل)", "url": "/hr_employees.html"},
                {"text": "📅 شاشة الحضور والانصراف", "url": "/hr_attendance.html"},
                {"text": "💸 شاشة السلف والقروض", "url": "/hr_loans.html"},
                {"text": "🧮 شاشة مسير الرواتب", "url": "/hr_payroll.html"},
                {"text": "🖨️ كشف المرتبات للطباعة", "url": f"{P}/hr/reports/payroll-sheet/print?period={cur_period}"},
            ],
        })
    finally:
        db.close()

    link_index = [
        {"module": m["name"], "name": l["text"].split(" ", 1)[-1], "url": l["url"]}
        for m in modules for l in m["links"]
    ]

    # ---------- الشاشات التفصيلية لكل قسم (تبويبات) ----------
    def _scr(text, url):
        return {"text": text, "url": url}

    screens = [
        {
            "name": "المحاسبة والأستاذ العام", "icon": "🧾", "color": "#1e5b93",
            "description": "الدليل المحاسبي، القيود اليومية بالترحيل المزدوج، والتقارير المالية بسنوات المقارنة.",
            "groups": [
                {"title": "الشاشة الأساسية", "screens": [
                    _scr("🏠 الشاشة الرئيسة (لوحة القيادة)", "/"),
                    _scr("⚙️ Swagger / تفاعل API", "/docs"),
                ]},
                {"title": "البيانات المحاسبية", "screens": [
                    _scr("🏦 دليل الحسابات", f"{P}/accounting/accounts"),
                    _scr("📒 دفتر القيود اليومية", f"{P}/accounting/journal-entries"),
                ]},
                {"title": "تقارير JSON", "screens": [
                    _scr("📊 ميزان المراجعة", f"{P}/accounting/reports/trial-balance"),
                    _scr("📈 المركز المالي (مقارن)", f"{P}/accounting/reports/comparative/balance-sheet?as_of={today_s}&prior_as_of={prior_y}"),
                    _scr("📉 قائمة الدخل (مقارنة سنوية)", f"{P}/accounting/reports/comparative/income-statement?date_from={jan1}&date_to={today_s}&comparison_type=yoy"),
                    _scr("💵 التدفقات النقدية (مقارنة)", f"{P}/accounting/reports/comparative/cash-flow?date_from={jan1}&date_to={today_s}&comparison_type=yoy"),
                ]},
                {"title": "شاشات الطباعة", "screens": [
                    _scr("🖨️ طباعة المركز المالي", f"{P}/accounting/reports/print/balance-sheet?as_of={today_s}&prior_as_of={prior_y}"),
                    _scr("🖨️ طباعة قائمة الدخل", f"{P}/accounting/reports/print/income-statement?date_from={jan1}&date_to={today_s}&comparison_type=yoy"),
                    _scr("🖨️ طباعة التدفقات النقدية", f"{P}/accounting/reports/print/cash-flow?date_from={jan1}&date_to={today_s}&comparison_type=yoy"),
                ]},
            ],
        },
        {
            "name": "المخازن والجرد", "icon": "📦", "color": "#2e7d32",
            "description": "الأصناف والمخازن، الحركات (وارد/منصرف/تحويل/تسوية)، الجرد الدوري، وتقييم IAS 2.",
            "groups": [
                {"title": "البيانات الرئيسية", "screens": [
                    _scr("🏷️ بطاقات الأصناف", f"{P}/inventory/items"),
                    _scr("🏬 المخازن", f"{P}/inventory/warehouses"),
                ]},
                {"title": "الحركات والجرد", "screens": [
                    _scr("🔄 سجل الحركات (دفتر السلامة)", f"{P}/inventory/movements"),
                    _scr("📋 أوراق الجرد والتسويات", f"{P}/inventory/counts"),
                ]},
                {"title": "التقارير", "screens": [
                    _scr("📈 الرصيد الحالي لكل مخزن", f"{P}/inventory/reports/stock"),
                    _scr("💰 تقرير التقييمIAS 2", f"{P}/inventory/reports/valuation"),
                ]},
            ],
        },
        {
            "name": "المبيعات والعملاء", "icon": "🛒", "color": "#ef6c00",
            "description": "فواتير البيع بخصم المخزون لحظيًا وقيد الإيراد والضريبة، وأرصدة العملاء.",
            "groups": [
                {"title": "البيانات الرئيسية", "screens": [
                    _scr("👥 العملاء وأرصدتهم", f"{P}/sales/customers"),
                ]},
                {"title": "الفواتير", "screens": [
                    _scr("🧾 فواتير البيع", f"{P}/sales/invoices"),
                ]},
            ],
        },
        {
            "name": "المشتريات والموردون", "icon": "🚚", "color": "#6a1b9a",
            "description": "دورة المشتريات الكاملة: طلب الحاجة ← أمر شراء ← إذن استلام GRN ← فاتورة بمطابقة ثلاثية.",
            "groups": [
                {"title": "البيانات الرئيسية", "screens": [
                    _scr("🤝 الموردون", f"{P}/purchasing/suppliers"),
                    _scr("📄 طلبات الاحتياجات MR", f"{P}/purchasing/material-requests"),
                    _scr("🚛 أذون التوريد الواردة DN", f"{P}/purchasing/delivery-notes"),
                ]},
                {"title": "أوامر الشراء والاستلام", "screens": [
                    _scr("📝 أوامر الشراء PO", f"{P}/purchasing/orders"),
                    _scr("📥 أذون الاستلام GRN", f"{P}/purchasing/receipts"),
                ]},
                {"title": "الفوترة", "screens": [
                    _scr("🧾 فواتير الشراء", f"{P}/purchasing/invoices"),
                ]},
            ],
        },
        {
            "name": "الخزينة والمالية", "icon": "💰", "color": "#00695c",
            "description": "النقدية والشيكات والفيزا والتحويلات، ورديات أمين الخزينة، والتسويات البنكية.",
            "groups": [
                {"title": "البيانات الرئيسية", "screens": [
                    _scr("🏦 الصناديق والبنوك", f"{P}/treasury/accounts"),
                    _scr("⏱️ ورديات أمين الخزينة", f"{P}/treasury/shifts"),
                ]},
                {"title": "السندات والمدفوعات", "screens": [
                    _scr("🧾 سندات القبض والصرف", f"{P}/treasury/vouchers"),
                    _scr("📄 دورة الشيكات الواردة/الصادرة", f"{P}/treasury/checks"),
                ]},
                {"title": "التسويات", "screens": [
                    _scr("🔁 التسويات البنكية", f"{P}/treasury/reconciliations"),
                ]},
            ],
        },
        {
            "name": "الموارد البشرية والرواتب", "icon": "👥", "color": "#c62828",
            "description": "ملفات العاملين، الحضور والانصراف، السلف والقروض بأقساطها، ومسير الرواتب مع الترحيل المحاسبي الآلي.",
            "groups": [
                {"title": "الشاشات التفاعلية (إدخال مباشر)", "screens": [
                    _scr("🏢 الأقسام والفروع — شاشة إدخال", "/hr_departments.html"),
                    _scr("🗂️ الموظفون — شاشة تعيين/تعديل", "/hr_employees.html"),
                    _scr("📅 الحضور والانصراف — شاشة تسجيل", "/hr_attendance.html"),
                    _scr("💸 السلف والقروض — شاشة دورة الصرف", "/hr_loans.html"),
                    _scr("🧮 مسير الرواتب — توليد/اعتماد/صرف", "/hr_payroll.html"),
                ]},
                {"title": "بيانات JSON (للمطورين/Try-it-out)", "screens": [
                    _scr("🏢 قائمة الأقسام", f"{P}/hr/departments"),
                    _scr("🌐 قائمة الفروع", f"{P}/hr/branches"),
                    _scr("🗂️ ملف الموظفين", f"{P}/hr/employees"),
                    _scr("✅ سجل الحضور الشهري", f"{P}/hr/attendance?period={cur_period}"),
                    _scr("💸 السلف وأقساطها", f"{P}/hr/loans"),
                    _scr("🧮 مسيرات الرواتب", f"{P}/hr/payrolls"),
                ]},
                {"title": "الطباعة", "screens": [
                    _scr("🖨️ كشف المرتبات للطباعة", f"{P}/hr/reports/payroll-sheet/print?period={cur_period}"),
                ]},
            ],
        },
        {
            "name": "المستخدمون والصلاحيات", "icon": "🔐", "color": "#3f5567",
            "description": "تسجيل الدخول JWT، إدارة المستخدمين والأدوار (RBAC)، وسجل تدقيق الدخول.",
            "groups": [
                {"title": "الجلسات", "screens": [
                    _scr("🚪 شاشة تسجيل الدخول (HTML)", "/login"),
                    _scr("🔁 تحديث الرمز (POST)", f"{P}/auth/refresh"),
                    _scr("🚪 تسجيل الخروج (POST)", f"{P}/auth/logout"),
                    _scr("🙋 بيانات المستخدم الحالي", f"{P}/auth/me"),
                ]},
                {"title": "الإدارة", "screens": [
                    _scr("👤 المستخدمون", f"{P}/auth/users"),
                    _scr("🎭 الأدوار المتاحة", f"{P}/auth/roles"),
                    _scr("📜 سجل تدقيق الدخول", f"{P}/auth/audit"),
                ]},
            ],
        },
    ]

    # دمج شاشات التبويبات في دليل الروابط الشامل (منع التكرار بنفس المسار)
    seen_urls = {r["url"] for r in link_index}
    for s in screens:
        for grp in s["groups"]:
            for sc in grp["screens"]:
                if sc["url"] not in seen_urls:
                    seen_urls.add(sc["url"])
                    link_index.append({"module": s["name"], "name": sc["text"].split(" ", 1)[-1], "url": sc["url"]})

    return tpl.TemplateResponse(request, "dashboard.html", {
        "app_name": settings.APP_NAME,
        "version": "1.0.0",
        "prefix": P,
        "current_period": cur_period,
        "today_s": today_s,
        "jan1": jan1,
        "prior_y": prior_y,
        "db_url_short": settings.DATABASE_URL.replace("sqlite:///", "SQLite: "),
        "modules": modules,
        "screens": screens,
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
    from backend.modules.auth.router import router as auth_router

    app.include_router(accounting_router, prefix=settings.API_V1_PREFIX)
    app.include_router(accounting_reports_router, prefix=settings.API_V1_PREFIX)
    app.include_router(inventory_router, prefix=settings.API_V1_PREFIX)
    app.include_router(sales_router, prefix=settings.API_V1_PREFIX)
    app.include_router(purchasing_router, prefix=settings.API_V1_PREFIX)
    app.include_router(treasury_router, prefix=settings.API_V1_PREFIX)
    app.include_router(auth_router, prefix=settings.API_V1_PREFIX)

    # قسم الموارد البشرية يُفعَّل تلقائيًا بمجرد إضافة router.py داخل مجلده
    try:
        from backend.modules.hr.router import router as hr_router
        app.include_router(hr_router, prefix=settings.API_V1_PREFIX)
    except ModuleNotFoundError:
        pass


register_routers(app)
