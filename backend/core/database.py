# -*- coding: utf-8 -*-
"""قاعدة بيانات مشتركة (SQLAlchemy) — كل قسم يعرّف جداوله في models.py الخاص به.

القاعدة واحدة، لكن كل قسم يستورد Base من هنا فقط، ولا يوجد أي
استيراد متبادل مباشر بين مجلدات الأقسام؛ الروابط تتم عبر واجهات API فقط.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

settings = get_settings()

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
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
