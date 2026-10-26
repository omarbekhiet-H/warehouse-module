# -*- coding: utf-8 -*-
"""الإعدادات العامة للنظام — ملف مركزي واحد يشترك فيه كل الأقسام.

كل قسم (محاسبة/مخازن/مبيعات/مشتريات/موارد بشرية) لا يقرأ الإعدادات مباشرة،
بل يمررها الـ Router الخاص به عبر deps() الموحّدة في هذا الملف.
"""
from __future__ import annotations

import os
from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic_settings import BaseSettings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


class Settings(BaseSettings):
    APP_NAME: str = "ERP System"
    API_V1_PREFIX: str = "/api/v1"
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./erp.db")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    CORS_ORIGINS: list[str] = ["*"]

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    settings: Settings = Depends(get_settings),
) -> dict:
    """فحص الصلاحيات المشترك بين جميع الأقسام.

    ملاحظة (سبب الخطأ السابق): كان تحديث user_id معطّل في النسخ القديمة؛
    الآن يُعاد بناء كائن المستخدم من الـ Token في كل طلب.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
        )
    # TODO: استبدال الفحص الوهمي بفك تشفير JWT حقيقي عند ربط نظام المصادقة
    from jose import jwt

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return {
        "user_id": payload.get("sub"),
        "username": payload.get("username"),
        "roles": payload.get("roles", []),
    }


def module_deps(prefix: str):
    """مولّد اعتماديات موحّد لكل قسم لضمان نفس شكل الاستجابة والوثائق."""
    async def _health():
        return {"module": prefix, "status": "ok"}
    return _health
