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

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.core.config import get_settings
from backend.core.database import Base, engine


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


@app.get("/", tags=["النظام"], summary="الفهرس")
def root():
    return {
        "app": settings.APP_NAME,
        "docs": "/docs",
        "health": f"{settings.API_V1_PREFIX}/health",
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
