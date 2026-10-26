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
    import backend.modules.hr.models           # noqa: F401
    Base.metadata.create_all(bind=engine)
    yield


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
            f"{settings.API_V1_PREFIX}/hr",
        ],
    }


def register_routers(app: FastAPI) -> None:
    """تسجيل راوترات الأقسام — كل قسم في ملفاته الخاصة ولا يستورد من main."""
    from backend.modules.accounting.router import router as accounting_router
    from backend.modules.inventory.router import router as inventory_router
    from backend.modules.sales.router import router as sales_router
    from backend.modules.purchasing.router import router as purchasing_router

    app.include_router(accounting_router, prefix=settings.API_V1_PREFIX)
    app.include_router(inventory_router, prefix=settings.API_V1_PREFIX)
    app.include_router(sales_router, prefix=settings.API_V1_PREFIX)
    app.include_router(purchasing_router, prefix=settings.API_V1_PREFIX)

    # قسم الموارد البشرية يُفعَّل تلقائيًا بمجرد إضافة router.py داخل مجلده
    try:
        from backend.modules.hr.router import router as hr_router
        app.include_router(hr_router, prefix=settings.API_V1_PREFIX)
    except ModuleNotFoundError:
        pass


register_routers(app)
