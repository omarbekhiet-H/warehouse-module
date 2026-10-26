# -*- coding: utf-8 -*-
"""قاعدة بيانات مشتركة (SQLAlchemy) — كل قسم يعرّف جداوله في models.py الخاص به.

القاعدة واحدة، لكن كل قسم يستورد Base من هنا فقط، ولا يوجد أي
استيراد متبادل مباشر بين مجلدات الأقسام؛ الروابط تتم عبر واجهات API فقط.
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

settings = get_settings()

_is_sqlite = "sqlite" in settings.DATABASE_URL

engine = create_engine(
    settings.DATABASE_URL,
    # SQLite (تطوير): خيط واحد مسموح. PostgreSQL (إنتاج): تجميعة اتصالات آمنة.
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    **({} if _is_sqlite else {
        "pool_size": int(os.getenv("DB_POOL_SIZE", "10")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "20")),
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }),
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
